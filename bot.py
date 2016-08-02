from datetime import datetime
import argparse
import json
import threading
import time

import urllib
import httplib
import db

# for web server
import requests

from flask import Flask, request, render_template
from flask_googlemaps import GoogleMaps
from flask_googlemaps import Map
from flask_googlemaps import icons
from requests.packages.urllib3.exceptions import InsecureRequestWarning

import utils

global slack_webhook_urlpath
slack_webhook_urlpath = "https://hooks.slack.com/services/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"

global pokemon_icons_prefix
pokemon_icons_prefix = ":pokemon-"
	 
with open('locales/pokemon.en.json') as f:
	pokemon_names = json.load(f)

worker_no = 0
workers = {}
notified_pokemon = {}

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

	alert_text = 'I\'m just <http://mygeoposition.com/?q=' + str(pokemon.lat) + ',' + str(pokemon.lon) + '>' + \
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

# fake web app for heroku
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

# for flask heroku
with open('credentials.json') as f:
    credentials = json.load(f)

GOOGLEMAPS_KEY = credentials.get('gmaps_key', None)
AUTO_REFRESH = 45  # refresh map every X s
app = create_app()

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
    map_center = utils.get_map_center()
    fullmap = Map(
        identifier='fullmap2',
        style='height:100%;width:100%;top:0;left:0;position:absolute;z-index:200;',
        lat=map_center[0],
        lng=map_center[1],
        markers=[],  # will be fetched by browser
        zoom='15',
    )
    return fullmap

if __name__ == '__main__':
	spawn_workers(workers)
	args = get_args()
	app.run(debug=True, threaded=True, host=args.host, port=args.port)