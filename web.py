# -*- coding: utf-8 -*-
from datetime import datetime
import argparse
import json
import threading
import time

import urllib
import httplib


import requests
from flask import Flask, request, render_template
from flask_googlemaps import GoogleMaps
from flask_googlemaps import Map
from flask_googlemaps import icons
from requests.packages.urllib3.exceptions import InsecureRequestWarning

import config as app_config
import db
import utils

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


# Check whether config has all necessary attributes
REQUIRED_SETTINGS = (
    'GRID',
    'AREA_NAME',
    'REPORT_SINCE',
)
for setting_name in REQUIRED_SETTINGS:
    if not hasattr(app_config, setting_name):
        raise RuntimeError('Please set "{}" in config'.format(setting_name))


with open('credentials.json') as f:
    credentials = json.load(f)

with open('locales/pokemon.en.json') as f:
    pokemon_names = json.load(f)


GOOGLEMAPS_KEY = credentials.get('gmaps_key', None)
AUTO_REFRESH = 45  # refresh map every X s


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-H',
        '--host',
        help='Set web server listening host',
        default='127.0.0.1'
    )
    parser.add_argument(
        '-P',
        '--port',
        type=int,
        help='Set web server listening port',
        default=5000
    )
    parser.add_argument(
        '-d', '--debug', help='Debug Mode', action='store_true'
    )
    parser.set_defaults(DEBUG=True)
    return parser.parse_args()


def create_app():
    app = Flask(__name__, template_folder='templates')
    GoogleMaps(app, key=GOOGLEMAPS_KEY)
    return app


app = create_app()


@app.route('/data')
def pokemon_data():
    return json.dumps(get_pokemarkers())


@app.route('/workers_data')
def workers_data():
    return json.dumps(get_worker_markers())


@app.route('/config')
def config():
    """Gets the settings for the Google Maps via REST"""
    map_center = get_map_center()
    return json.dumps({
        'lat': map_center[0],
        'lng': map_center[1],
        'zoom': 17,
        'identifier': 'fullmap'
    })


@app.route('/')
def fullmap():
    return render_template(
        'map.html',
        key=GOOGLEMAPS_KEY,
        fullmap=get_map(),
        auto_refresh=AUTO_REFRESH * 1000
    )


def get_pokemarkers():
    markers = []
    session = db.Session()
    pokemons = db.get_sightings(session)
    session.close()

    for pokemon in pokemons:
        name = pokemon_names[str(pokemon.pokemon_id)]
        datestr = datetime.fromtimestamp(pokemon.expire_timestamp)
        dateoutput = datestr.strftime("%H:%M:%S")

        LABEL_TMPL = u'''
<div><b>{name}</b><span> - </span><small><a href='http://www.pokemon.com/us/pokedex/{id}' target='_blank' title='View in Pokedex'>#{id}</a></small></div>
<div>Disappears at - {disappear_time_formatted} <span class='label-countdown' disappears-at='{disappear_time}'></span></div>
<div><a href='https://www.google.com/maps/dir/Current+Location/{lat},{lng}' target='_blank' title='View in Maps'>Get Directions</a></div>
'''
        label = LABEL_TMPL.format(
            id=pokemon.pokemon_id,
            name=name,
            disappear_time=pokemon.expire_timestamp,
            disappear_time_formatted=dateoutput,
            lat=pokemon.lat,
            lng=pokemon.lon,
        )
        #  NOTE: `infobox` field doesn't render multiple line string in frontend
        label = label.replace('\n', '')

        markers.append({
            'type': 'pokemon',
            'name': name,
            'key': '{}-{}'.format(pokemon.pokemon_id, pokemon.spawn_id),
            'disappear_time': pokemon.expire_timestamp,
            'icon': 'static/icons/%d.png' % pokemon.pokemon_id,
            'lat': pokemon.lat,
            'lng': pokemon.lon,
            'pokemon_id': pokemon.pokemon_id,
            'infobox': label
        })

    return markers


def get_worker_markers():
    markers = []
    points = utils.get_points_per_worker()
    # Worker start points
    for worker_no, worker_points in enumerate(points):
        coords = utils.get_start_coords(worker_no)
        markers.append({
            'icon': icons.dots.green,
            'lat': coords[0],
            'lng': coords[1],
            'infobox': 'Worker %d' % worker_no,
            'type': 'custom',
            'subtype': 'worker',
            'key': 'start-position-%d' % worker_no,
            'disappear_time': -1
        })
        # Circles
        for i, point in enumerate(worker_points):
            markers.append({
                'lat': point[0],
                'lng': point[1],
                'infobox': 'Worker %d point %d' % (worker_no, i),
                'subtype': 'point',
            })
    return markers


def get_map():
    if request.args.get('lat') and request.args.get('lat'):
        map_center = float(request.args.get('lat')), float(request.args.get('lon'))
        app_config.OVERRIDE_LOCATION = map_center
    else:
        app_config.OVERRIDE_LOCATION = None
    map_center = get_map_center()
    fullmap = Map(
        identifier='fullmap2',
        style='height:100%;width:100%;top:0;left:0;position:absolute;z-index:200;',
        lat=map_center[0],
        lng=map_center[1],
        markers=[],  # will be fetched by browser
        zoom='15',
    )
    return fullmap

def get_map_center():
    if app_config.OVERRIDE_LOCATION:
        map_center = app_config.OVERRIDE_LOCATION
    else:
        map_center = utils.get_map_center()
    return map_center

@app.route('/report')
def report_main():
    session = db.Session()
    top_pokemon = db.get_top_pokemon(session)
    bottom_pokemon = db.get_top_pokemon(session, order='ASC')
    bottom_sightings = db.get_all_sightings(
        session, [r[0] for r in bottom_pokemon]
    )
    stage2_pokemon = db.get_stage2_pokemon(session)
    stage2_sightings = db.get_all_sightings(
        session, [r[0] for r in stage2_pokemon]
    )
    js_data = {
        'charts_data': {
            'punchcard': db.get_punch_card(session),
            'top30': [(pokemon_names[str(r[0])], r[1]) for r in top_pokemon],
            'bottom30': [
                (pokemon_names[str(r[0])], r[1]) for r in bottom_pokemon
            ],
            'stage2': [
                (pokemon_names[str(r[0])], r[1]) for r in stage2_pokemon
            ],
        },
        'maps_data': {
            'bottom30': [sighting_to_marker(s) for s in bottom_sightings],
            'stage2': [sighting_to_marker(s) for s in stage2_sightings],
        },
        'map_center': utils.get_map_center(),
        'zoom': 13,
    }
    icons = {
        'top30': [(r[0], pokemon_names[str(r[0])]) for r in top_pokemon],
        'bottom30': [(r[0], pokemon_names[str(r[0])]) for r in bottom_pokemon],
        'stage2': [(r[0], pokemon_names[str(r[0])]) for r in stage2_pokemon],
        'nonexistent': [
            (r, pokemon_names[str(r)])
            for r in db.get_nonexistent_pokemon(session)
        ]
    }
    session_stats = db.get_session_stats(session)
    session.close()

    area = utils.get_scan_area()

    return render_template(
        'report.html',
        current_date=datetime.now(),
        area_name=app_config.AREA_NAME,
        area_size=area,
        total_spawn_count=session_stats['count'],
        spawns_per_hour=session_stats['per_hour'],
        session_start=session_stats['start'],
        session_end=session_stats['end'],
        session_length_hours=int(session_stats['length_hours']),
        js_data=js_data,
        icons=icons,
    )


@app.route('/report/<int:pokemon_id>')
def report_single(pokemon_id):
    session = db.Session()
    session_stats = db.get_session_stats(session)
    js_data = {
        'charts_data': {
            'hours': db.get_spawns_per_hour(session, pokemon_id),
        },
        'map_center': utils.get_map_center(),
        'zoom': 13,
    }
    session.close()
    return render_template(
        'report_single.html',
        current_date=datetime.now(),
        area_name=app_config.AREA_NAME,
        area_size=utils.get_scan_area(),
        pokemon_id=pokemon_id,
        pokemon_name=pokemon_names[str(pokemon_id)],
        total_spawn_count=db.get_total_spawns_count(session, pokemon_id),
        session_start=session_stats['start'],
        session_end=session_stats['end'],
        session_length_hours=int(session_stats['length_hours']),
        js_data=js_data,
    )


def sighting_to_marker(sighting):
    return {
        'icon': '/static/icons/{}.png'.format(sighting.pokemon_id),
        'lat': sighting.lat,
        'lon': sighting.lon,
    }


@app.route('/report/heatmap')
def report_heatmap():
    session = db.Session()
    pokemon_id = request.args.get('id')
    points = db.get_all_spawn_coords(session, pokemon_id=pokemon_id)
    session.close()
    return json.dumps(points)

# all the stuff for bot?
worker_no = 0
workers = {}
notified_pokemon = {}

global slack_webhook_urlpath
slack_webhook_urlpath = "https://hooks.slack.com/services/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"

global pokemon_icons_prefix
pokemon_icons_prefix = ":pokemon-"

boring_pokemon_id = [41, 16, 19, 23, 29, 32, 10, 13, 21, 43, 46, 52, 54, 60, 69, 72, 98, 109, 116, 118, 120, 129, 133]

def unix_time_millis(dt):
    epoch = datetime.utcfromtimestamp(0)
    return (dt - epoch).total_seconds()

last_time_checked = int(unix_time_millis(datetime.utcnow()))

def update_last_time_checked():
    last_time_checked = int(unix_time_millis(datetime.utcnow()))

def work(worker_no):
    session = db.Session()
    pokemons = db.get_sightings(session)

    update_last_time_checked()

    current_pokemon = []
    for pokemon in pokemons:
        current_pokemon.append(pokemon.id)
        if pokemon.expire_timestamp > last_time_checked and pokemon.id not in notified_pokemon:
            name = pokemon_names[str(pokemon.pokemon_id)]
            notified_pokemon[pokemon.id] = True
            print("found pokemon ", name)
            if pokemon.pokemon_id not in boring_pokemon_id:
                print("Sending pokemon to slack ", name)
                send_pokemon_to_slack(pokemon)

    notified_pokemon_ids = notified_pokemon.keys()
    for pokemon_id in notified_pokemon_ids:
        if pokemon_id not in current_pokemon:
            del notified_pokemon[pokemon_id]

    session.close()

    time.sleep(15)
    start_worker(worker_no)

def start_worker(worker_no):
    # Ok I NEED to global this here
    global workers
    print('[W%d] Worker (re)starting up!' % worker_no)
    worker = threading.Thread(target=work, args=[worker_no])
    worker.daemon = True
    worker.name = 'worker-%d' % worker_no
    worker.start()
    workers[worker_no] = worker


def spawn_workers(workers):
    count = 1
    for worker_no in range(count):
        start_worker(worker_no)
    while True:
        time.sleep(1)

def send_pokemon_to_slack(pokemon):
    pokename = pokemon_names[str(pokemon.pokemon_id)]

    disappear_datetime = datetime.fromtimestamp(pokemon.expire_timestamp)
    time_till_disappears = disappear_datetime - datetime.now()
    disappear_hours, disappear_remainder = divmod(time_till_disappears.seconds, 3600)
    disappear_minutes, disappear_seconds = divmod(disappear_remainder, 60)
    disappear_minutes = str(disappear_minutes)
    disappear_seconds = str(disappear_seconds)
    if len(disappear_seconds) == 1:
        disappear_seconds = str(0) + disappear_seconds
    disappear_time = disappear_datetime.strftime("%H:%M:%S")

    alert_text = 'I\'m just <http://https://poke-slack-rohan.herokuapp.com/?lat=' + str(pokemon.lat) + '&lon=' + str(pokemon.lon) + '>' + \
                 '|' + ' until ' + disappear_time + \
                ' (' + disappear_minutes + ':' + disappear_seconds + ')!'

    if pokemon_icons_prefix != ':pokeball:':
        user_icon = pokemon_icons_prefix + pokename.lower() + ':'
    else:
        user_icon = ':pokeball:'

    send_to_slack(alert_text, pokename, user_icon, slack_webhook_urlpath)

def send_to_slack(text, username, icon_emoji, webhook):
    data = urllib.urlencode({'payload': '{"username": "' + username + '", '
                                        '"icon_emoji": "' + icon_emoji + '", '
                                        '"text": "' + text + '"}'
                             })
    print username, icon_emoji, webhook

    h = httplib.HTTPSConnection('hooks.slack.com')
    headers = {"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}

    h.request('POST', webhook, data, headers)
    r = h.getresponse()
    ack = r.read()

@app.route('/start')
def start_worker_app():
    spawn_workers(workers)
    return json.dumps({})

if __name__ == '__main__':
    args = get_args()
    app.run(debug=True, threaded=True, host=args.host, port=args.port)
