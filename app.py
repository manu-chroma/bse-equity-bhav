import cherrypy
import requests
import redis
import pytz
from jinja2 import Environment, FileSystemLoader

import csv
import datetime
from datetime import timedelta
import json
import operator
import os
import zipfile

from io import BytesIO

# point jinja2 to templates folder
env = Environment(loader=FileSystemLoader('templates'))

# IST timezone
IST_TZ = pytz.timezone('Asia/Kolkata')


class BhavBackend:

    def __init__(self):
        # base url for data
        self.BASE_URL_BHAV_COPY = 'https://www.bseindia.com/download/BhavCopy/Equity/EQ{date}_CSV.ZIP'

        REDIS_URL = os.environ.get("REDIS_URL", 'redis://localhost:6379')
        self.rd = redis.StrictRedis.from_url(REDIS_URL,
                                             db=0,
                                             decode_responses=True,
                                             encoding='utf-8')

        self.last_db_update_time = None

    @cherrypy.expose
    def refresh_bhav(self):
        try:
            data = self.get_bhav_data_current()
            self._update_database(data)
        except Exception as e:
            cherrypy.log(e)
            return json.dumps({"status": "FAIL"})

        return json.dumps({"status": "SUCCESS"})

    def get_bhav_data_current(self):
        # create data url
        current_date = datetime.datetime.now(tz=IST_TZ)

        request_data_url = self.BASE_URL_BHAV_COPY.format(date=current_date.strftime('%d%m%y'))
        cherrypy.log("Fetching data from.." + request_data_url)
        self.last_db_update_time = current_date
        res = self._fetch_data(request_data_url)

        # check if data is available for today
        # if not, fetch data for the previous day

        # no zip file as response
        if 'text/html' in res.headers['content-type']:
            prev_trading_day = self._last_weekday(current_date)

            request_data_url = self.BASE_URL_BHAV_COPY.format(date=prev_trading_day.strftime('%d%m%y'))
            cherrypy.log("Fetching data from.." + request_data_url)
            res = self._fetch_data(request_data_url)
            self.last_db_update_time = prev_trading_day

        # parse csv data
        csv_file = self._unzip_data(BytesIO(res.content))
        bhav_data = []
        with open(csv_file, encoding='utf-8', mode='r') as f:
            reader = csv.DictReader(f)

            for row in reader:
                bhav_data.append(row)

        return bhav_data

    # home page
    @cherrypy.expose
    def index(self):
        template = env.get_template('index.html')

        top10_close = json.loads(self.rd.get("top10_close"))
        return template.render(top10_close=top10_close,
                               date=self.last_db_update_time.strftime("%d/%m/%y"))

    # autocomplete feature for search
    @cherrypy.expose
    def autocomplete(self, query_str: str):
        query_str = query_str.upper()
        # restrict result set size
        result_list = self.rd.keys("*" + query_str + "*")[:15]
        return json.dumps({
            "autocomplete": result_list
        })

    # single row result
    @cherrypy.expose
    def result(self, query_str):

        # wildcard and double query because of potential spaces
        result_list = self.rd.get(self.rd.keys("*" + query_str + "*")[0])
        return json.dumps({
            "result_set": json.loads(result_list)
        })

    # no trading happens on weekend
    # so skip those days
    def _last_weekday(self, date: datetime.datetime):

        weekday = date.weekday()

        days_to_skip = skip = {
            6: 2,  # sunday -> friday
            0: 3  # mon -> friday
        }.get(weekday, 1)  # default fallback to 1 day

        return date - timedelta(days=days_to_skip)

    def _fetch_data(self, request_data_url: str) -> requests.Response:
        try:
            res = requests.get(request_data_url)
        except Exception as e:
            cherrypy.log(e)

        return res

    def _update_database(self, bhav_data):
        for row in bhav_data:
            # todo: trim names before entering
            self.rd.set(row['SC_NAME'], json.dumps({
                'SC_CODE': row['SC_CODE'],
                'SC_NAME': row['SC_NAME'],
                'OPEN': row['OPEN'],
                'LOW': row['LOW'],
                'HIGH': row['HIGH'],
                'CLOSE': row['CLOSE']
            }))

        top10_close = []
        for row in sorted(bhav_data, key=lambda x: float(x['CLOSE']), reverse=True)[:10]:
            top10_close.append({
                'SC_CODE': row['SC_CODE'],
                'SC_NAME': row['SC_NAME'],
                'OPEN': row['OPEN'],
                'LOW': row['LOW'],
                'HIGH': row['HIGH'],
                'CLOSE': row['CLOSE']
            })

        self.rd.set("top10_close", json.dumps(top10_close))

    # assumption: there is only one file inside the folder
    def _unzip_data(self, zip_file: BytesIO) -> str:
        zip_reference = zipfile.ZipFile(zip_file)
        zip_reference.extract(zip_reference.namelist()[0])
        return zip_reference.namelist()[0]


if __name__ == '__main__':
    backend = BhavBackend()
    backend.refresh_bhav()

    HOST = '0.0.0.0'
    PORT = int(os.environ.get('PORT', '5010'))
    cherrypy.config.update({
        'server.socket_host': HOST,
        'server.socket_port': PORT,
    })
    cherrypy.quickstart(backend)
