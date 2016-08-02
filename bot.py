from datetime import datetime
import argparse
import json
import threading
import time

import urllib
import httplib
import db

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

if __name__ == '__main__':
	spawn_workers(workers)