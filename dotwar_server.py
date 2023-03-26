import datetime
import os
import json
import copy
from urllib.parse import unquote

from flask import Flask, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from sqlalchemy import or_, Date, case

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = "mysql://dotwar_server:dotwar_password01#!@127.0.0.1:3306/dotwar_db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'this is the qasc administrative secret key #?!'
db = SQLAlchemy(app)

# server_time = datetime.datetime.now()
# constants:K
AU_TO_KM = 1.495979e8  # 1 AU = that many kilometers
LIGHTSPEED = 1079251200  # km/hr

# parameters:
ENCOUNTER_RADIUS = 1.6e7  # kilometers
CAPTURE_RADIUS = 1.6e7  # kilometers
DEFENSE_RADIUS = 1.12e7  # kilometers
MAX_INSTANT_ACC = 1.6e7  # km/hr/hr
MAX_INSTANT_VEL = LIGHTSPEED
SIM_TICK = datetime.timedelta(seconds=1)  # in seconds.

system_path = ""
system_filename = ""
full_path = ""
game = None


def dist(a, b):
    c = [bj - ai for ai, bj in zip(a, b)]
    return abs(sum(ci ** 2 for ci in c)) ** 0.5


def mag(a):
    # print(f"vector {a} of type {type(a)}")
    return sum(ai ** 2 for ai in a) ** 0.5


class R(db.Model):
    __tablename__ = "r"
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Float)


class V(db.Model):
    __tablename__ = "v"
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Float)


class A(db.Model):
    __tablename__ = "a"
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Float)


class Pending(db.Model):
    __tablename__ = "pending"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer)
    value = db.Column(db.Float)


class Game(db.Model):
    __tablename__ = "game"
    id = db.Column(db.Integer, primary_key=True)
    myName = db.Column(db.String)
    created_on = db.Column(db.DateTime)
    last_modified = db.Column(db.DateTime)
    server_time = db.Column(db.DateTime)


class Entity(db.Model):
    __tablename__ = "entity"
    id = db.Column(db.Integer, primary_key=True)
    gameId = db.Column(db.Integer)
    myName = db.Column(db.String)
    captain = db.Column(db.String)
    r = db.Column(db.Integer)
    v = db.Column(db.Integer)
    a = db.Column(db.Integer)
    entity_type = db.Column(db.String)
    pending = db.Column(db.Integer)
    team = db.Column(db.Integer)
    created_on = db.Column(db.DateTime)
    authcode = db.Column(db.String)
    captured = db.Column(db.String)


class EventLog(db.Model):
    __tablename__ = "event_log"
    id = db.Column(db.Integer, primary_key=True)
    gameId = db.Column(db.Integer)
    createdAt = db.Column(db.DateTime)
    content = db.Column(db.String)

# Implemented endpoints:
#  /games *
#  /game/<myName>/status
#  /game/<myName>/scan
#  /game/<myName>/event_log, /game/<myName>/summary
#  /game/<myName>/agenda?vessel=&authcode=
#  /add_order?vessel=&authcode=&order={"task":"burn","args":{"a":[3d acceleration]}},"time":ISO date string}
# To do:
#  TODO: /game/<myName>/delete_order?vessel=&authcode=&order_id=
#  TODO: /play/<myName> returns the client, setup for the specified game
#  TODO: Convert all endpoints from GET to POST


def as_json(entity: Entity):

    pend_list = copy.deepcopy(get_pending(entity))

    for order in pend_list:
        order["time"] = order["time"].isoformat()

    json_compatible = {
        "myName": entity.myName,
        "captain": entity.captain,
        "r": entity.r.tolist(),
        "v": entity.v.tolist(),
        "a": entity.a.tolist(),
        "type": entity.entity_type,
        # <- can this be comprehended/lambda'd/mapped?
        "pending": pend_list,
        "team": entity.team,
        "created_on": entity.created_on.isoformat(),
    }

    if type(entity.authcode) is str:
        json_compatible["authcode"] = entity.authcode

    if type(entity.captured) is bool:
        json_compatible["captured"] = entity.captured

    return json_compatible


def add_order(entity: Entity, task, args, time):
    # order example: {"task":"burn", args:{"a":[0, 20, 1]}, "time":datetime}
    order = {
        "task": task,
        "args": args,
        "time": datetime.datetime.fromisoformat(time) if type(time) is str else time
    }

    if task == "burn":
        if "a" not in args:
            raise Exception("Burn order creation missing 'a' in args")
    if not all([type(e) in [int, float] for e in args["a"]]):
        raise Exception(
            f"Acceleration must be a list of integers or floats, not {[type(e) for e in args['a']]}")

    order["order_id"] = (max([pending_order["order_id"] for pending_order in entity.get_pending()]) + 1)\
        if len(entity.get_pending()) else 0
    order["parent_entity"] = entity.myName
    entity.pending.append(order)
    print("ADDING ORDER WITH TIME:", str(order["time"]))
    return order["order_id"]


def clear_order(entity: Entity, order_id):
    pass
    # pending = entity.get_pending()
    # valid = list(filter(lambda o: o["order_id"] != order_id, pending))
    # print(valid)
    # entity.pending = valid

# units are hours ! !!


def system_as_json():
    return {"game":
            {"myName": Game.query.first().myName,
             "created_on": Game.query.first().created_on.isoformat(),
             "last_modified": Game.query.first().last_modified.isoformat(),
             "server_time": Game.query.first().server_time.isoformat()
             },
            "entities": [as_json(entity) for entity in Entity.query.all()],
            "event_log": EventLog.query.all()
            }


def get_pending(entity_name=None):
    if entity_name:
        return sum([p.pending for p in Entity.query.filter_by(name=entity_name).all()]) + 0
    else:
        return sum([p.pending for p in Entity.query.all()]) + 0


def update_interval(interval: datetime.timedelta):
    # update interval of constant acceleration
    # interval should be in seconds
    this_moment = Game.query.first().server_time

    print("\nSTARTING NEW UPDATE SEGMENT FROM",
          str(this_moment),
          "TO", str(this_moment + interval),
          (" (interval of " + str(interval) + ")")
          )

    time = datetime.timedelta(seconds=0)  # elapsed time in seconds
    collisions = []
    instant = SIM_TICK  # time per tick in seconds
    while time < interval:
        for entity in Entity.query.all():

            # motion
            v = entity.v
            a = entity.a
            delta = instant.total_seconds() / 3600.0
            dr = v * delta + (1 / 2.0) * a * (delta ** 2.0)
            dv = a * delta
            step = [dr, dv]

            entity.r = entity.r + step[0]  # update each r by one instant
            entity.v = entity.v + step[1]  # update each v by one instant

            if mag(entity.v) > MAX_INSTANT_VEL:  # limit entities to lightspeed
                entity.v = (entity.v / mag(entity.v)) * \
                    MAX_INSTANT_VEL

        # print("TEST1 velocity:", Entity.query.filter_by(myName="TEST1").first())["v"])

        # filter collisions so objects remaining in radius after one timestep don't
        # keep generating collisions and events:
        collisions = []

        for entity_a in Entity.query.all():
            for entity_b in Entity.query.all() - entity_a:

                # capture check:
                if (entity_a.team == 1 and
                    entity_b.entity_type == "planet" and
                    entity_b.captured is not True and
                        (dist(entity_a.r, entity_b.r) <= CAPTURE_RADIUS)):
                    collisions.append([entity_a, entity_b, 'CAPTURE'])

                # defense check:
                if (entity_a.team == 0 and
                    entity_b.team == 1 and
                        dist(entity_a.r, entity_b.r) <= DEFENSE_RADIUS):
                    collisions.append([entity_a, entity_b, 'DEFENSE'])

        # find collisions that create events
        for collision in collisions:
            entity_a, entity_b, collision_type = collision
            if entity_a.entity_type == "craft":

                # capture check:
                if collision_type == 'CAPTURE':
                    event = {"type": "capture", "args": {"attacker": entity_a.myName, "planet": entity_b.myName},
                                     "time": (this_moment + time).isoformat()}
                    add_event(event)
                    entity_b.captured = True
                    print(event["args"]["attacker"], "captured planet", event["args"]["planet"], "at",
                          str(event.time))

                # defense check:
                elif collision_type == 'DEFENSE':
                    event = {"type": "defense", "args": {"defender": entity_a.myName, "victim": entity_b.myName},
                                     "time": (this_moment + time).isoformat()}
                    add_event(event)
                    session.delete(entity_b)
                    print(event["args"]["defender"], "destroyed vessel", event["args"]["victim"], "at",
                          str(event.time))
        time += instant

    Game.query.first().server_time += time
    print("ENDING SEGMENT AT SYSTEM TIME",
          str(Game.query.first().server_time))


def update(interval: datetime.timedelta):
    # update over period of time (interval, in hours), including orders and changes in acceleration.
    # collect orders in interval, and sort
    start_time = Game.query.first().server_time
    end_time = start_time + interval
            
    orders = [o for o in get_pending() if (o["time"] >= start_time) and (o["time"] <= end_time)]

    print("UNSORTED ORDER TIMES:", [str(order["time"]) for order in orders])

    orders = orders.sort(key=lambda o: o["time"])
    # print("SORTED ORDER TIMES:", [str(order["time"]) for order in orders])
    # update_interval for each interval
    for order in orders:
        update_interval(order["time"] - start_time)
        entity = Entity.query.filter_by(myName=order["parent_entity"]).first()
        if order["task"] == "burn":
            a = order["args"]["a"]
            # print(f"HANDLING ACCELERATION {a} of type {type(a)}")
            a = ((a / mag(a)) * MAX_INSTANT_ACC) if (
                mag(a) > MAX_INSTANT_ACC) else a  # limit acceleration to max
            entity.a = a
            add_event({'type': "burn",
                               "args": {
                                   "vessel": entity.myName,
                                   "a": order["args"]["a"],
                                   "position": (entity.r.tolist())
                               },
                       "time": order["time"].isoformat()
                       })

    # print("vessel", order["parent_entity"], "set new burn", order["args"], "at", str(order["time"]))
    # update remaining subinterval between last order and end of whole interval
    remaining_timedelta = end_time - \
        Game.query.first().server_time
    print("SEGMENTS DONE, REMAINING TIME", remaining_timedelta)
    update_interval(remaining_timedelta)
    print("FINISHED OVERALL UPDATE AT SYSTEM TIME",
          Game.query.first().server_time)
    # remove processed orders
    for order in orders:
        Entity.query.filter_by(myName=order["parent_entity"]).first(
        ).clear_order(order["order_id"])


def add_event(event):
    session.add(EventLog(content=event))
    session.commit()


def get_game_list():
    return [g.myName for g in Game.query.all()]


def valid_json(json_string):
    try:
        json.loads(json_string)
    except json.decoder.JSONDecodeError:
        return False
    return True


def valid_datetime(iso_string):
    try:
        datetime.datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
    except:
        return False
    return True


def select_err(err, use_html):
    return err if use_html else {"ok": False, "msg": err}


def try_authorize_vessel(vessel_name: str, authcode: str):
    try:

        vessel = Entity.query.filter_by(myName=vessel_name).first()

        if vessel is None:
            raise LookupError(f"No vessel named {vessel_name}.")

        if vessel.authcode is None:
            raise ValueError(f"Entity {vessel_name} has no authcode.")

        if authcode != vessel.authcode:
            raise PermissionError(
                f"Not authorized. {authcode} is not this vessel's authcode.")

    except LookupError as e:
        response.status = 404
        return {"ok": False, "msg": str(e)}
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    except PermissionError as e:
        response.status = 403
        return {"ok": False, "msg": str(e)}

    return vessel

# route for main page. not API. not POST.


@app.route('/', methods=["GET"])
def hello_world():
    return global_config["welcome"] + "<br>Running games:<br>" + "<br> ".join(
        [f"<a href='/play/{game}'>{game}</a>" for game in get_game_list()]) + """<hr>"""

# route to retrieve client. not API. not POST.


@app.route("/play/<myName>")
def play(myName):
    if myName not in get_game_list():
        response.status = 404
        return f"Couldn't find game <code>{myName}</code>."

    client_html = ""  # client template
    with open(os.path.join(global_config["static_dir"], "client.html")) as client_file:
        client_html = "".join(client_file.readlines())
    client_html = client_html.replace("{{GAMENAME}}", myName)
    return client_html


@app.route('/games', methods=["POST"])
def games():
    return {"ok": True, "games": get_game_list()}


@app.route('/game/<myName>', methods=["POST"])
@app.route('/game/<myName>/', methods=["POST"])
@app.route('/game/<myName>/status', methods=["POST"])
@app.route('/game/<myName>/status/', methods=["POST"])
def game_status(myName):
    update_to_now(myName)
    query = request.POST

    ret = {"ok": True, "game": None}

    g_json = system_as_json()["game"]
    ret["game"] = g_json
    if ("html" in query) and valid_json(query.html) and json.loads(query.html):
        return "<br>".join(["Game '" + myName + "' status:",
                            "Created on: " + g_json["created_on"] + " (" + datetime.datetime.fromisoformat(
                                g_json["created_on"]).strftime("%b %d %Y, %X") + ")",
                            "System time: " + g_json["server_time"] + " (" + datetime.datetime.fromisoformat(
                                g_json["server_time"]).strftime("%b %d %Y, %X") + ")"
                            ])
    return ret


@app.route("/game/<myName>/scan", methods=["POST"])
def scan(myName):
    update_to_now(myName)
    json_entities = system_as_json()["entities"]
    query = request.POST
    print("HTTP?", query.html)

    for entity in json_entities:
        for culled_attribute in ["pending", "authcode"]:
            if culled_attribute in entity.keys():
                del entity[culled_attribute]

    if ("filter" in query) and valid_json(query.filter):
        filters = json.loads(query.filter)
        json_entities = list(filter(lambda json_entity: all(
            [json_entity[k] == filters[k] for k in filters]), json_entities))
    elif ("filter" in query) and not valid_json(query.filter):
        return {"ok": False, "msg": "invalid JSON provided in 'filter'"}

    if ("html" in query) and valid_json(query.html) and json.loads(query.html):
        rows = [[json_entity["myName"], json_entity["type"], (json_entity["captain"] if json_entity["captain"] else "-----"),
                 f"<{json_entity['r'][0]:.3f} {json_entity['r'][1]:.3f} {json_entity['r'][2]:.3f}>",
                 f"<{json_entity['v'][0]:.3f} {json_entity['v'][1]:.3f} {json_entity['v'][2]:.3f}>",
                 f"<{json_entity['a'][0]:.3f} {json_entity['a'][1]:.3f} {json_entity['a'][2]:.3f}>",
                 ["Defenders", "Attackers", "Itself"][json_entity["team"]]
                 ] for json_entity in json_entities]

        # headers: list of table headers.
        # data_rows: list of rows of the table, each row a list of individual elements
        table_rows = [
            "<tr>" + ("".join(["<th>" + header + "</th>" for header in ["myName", "TYPE", "CAPTAIN", "POSITION", "HEADING", "ACCELERATION", "ALLEGIANCE"]])) + "</tr>"]
        for data_row in rows:
            table_rows.append(
                "<pre>" + (
                    "".join(["<td>" + element + "</td>" for element in data_row])
                ) + "</tr>"
            )
        table = "<table>" + "".join(table_rows) + "</table>"
        style_tag = """<style>
table {
	font-family: Roboto, sans-serif;
	border-collapse: collapse;
}

td, th {
	font-size: 14px;
	/*border: 1px solid #000000;*/
	text-align: left;
	padding: 5px;
}

tr:nth-child(even) {
	background-color: #dddddd;
}
</style>"""
        page = style_tag + table
        return page
    elif ("html" in query) and not valid_json(query.html):
        return {"ok": False, "msg": "invalid JSON provided in 'html'"}
    else:
        return {"ok": True, "entities": json_entities}


@app.route("/game/<myName>/event_log", methods=["POST"])
@app.route("/game/<myName>/summary", methods=["POST"])
def summary(myName):
    update_to_now(myName)
    query = request.POST

    query.start, query.end = query.start.strip(), query.end.strip()

    start = datetime.datetime.fromisoformat(query.start) if (
        query.start and valid_json(query.start)) else datetime.datetime.fromtimestamp(0)  # the epoch
    end = datetime.datetime.fromisoformat(query.end) if (
        query.end and valid_datetime(query.end)) else Game.query.first().server_time

    start = start if start else datetime.datetime.fromtimestamp(
        0)  # the epoch
    end = end if end else Game.query.first().server_time

    if start:
        events = []
        for event in EventLog.query.all():
            if (datetime.datetime.fromisoformat(event.time) >= start) and (
                    datetime.datetime.fromisoformat(event.time) <= end):
                events.append(event)
    else:
        events = EventLog.query.all()

    if ("start" in query and "end" in query) and not (valid_datetime(query.start) and valid_datetime(query.end)):
        return {"ok": False, "msg": "if used, start and end must be ISO datetime strings"}
    if "html" in query and valid_json(query.html) and json.loads(query.html):
        page = ["<pre>Events between " + (query.start if query.start else "system start") + " and " + (
            query.end if query.end else "current time:")]
        for event in events:
            time = datetime.datetime.fromisoformat(
                event.time).strftime("%b %d %Y, %X")
            desc = ""
            abbr = ""
            if event["type"] == "capture":
                desc = ["vessel", event["args"]["attacker"],
                        "captured", event["args"]["planet"]]
                abbr = "  [ATK] "
            elif event["type"] == "defense":
                desc = ["vessel", event["args"]["defender"],
                        "destroyed vessel", event["args"]["victim"]]
                abbr = "  [DEF] "
            elif event["type"] == "burn":
                desc = ["vessel", event["args"]["vessel"], "started burn", str(event["args"]["a"]), "while at coords",
                        str([float(format(value, ".3f")) for value in event["args"]["position"]])]
                abbr = "  [NAV] "
            desc = abbr.join([time, " ".join(desc)])
            page.append(desc)
        return "<br/>".join(page)

    else:
        return {"ok": True, "events": events}


@app.route("/game/<myName>/agenda", methods=["POST"])
def agenda(myName):
    update_to_now(myName)
    query = request.POST

    if "vessel" not in query:
        return {"ok": False, "msg": "Please provide a spacecraft myName as 'vessel' in query string."}

    if "authcode" not in query:
        return {"ok": False, "msg": "Please provide an authorization code as 'authcode' in query string."}

    auth = try_authorize_vessel(query.vessel, query.authcode)

    if type(auth) is Entity:
        vessel = auth
    else:
        return auth

    if ("html" in query) and valid_json(query.html) and json.loads(query.html):
        page = [f"<pre>Pending orders for vessel {vessel.myName}:"]
        for order in vessel.pending:
            page.append("at {}: burn [{:.3f} {:.3f} {:.3f}] ; order ID: {}"
                        .format(order["time"].strftime("%I:%M %p on %A, %b %d, %Y"),
                                *order["args"]["a"], order["order_id"]
                                )
                        )  # formerly format "%b %d %Y, %X"
        page = "<br>".join(page)
        return page
    else:
        for order in vessel.pending:
            order["time"] = order["time"].isoformat()
        return {"ok": True, "agenda": vessel.pending}


@app.route("/game/<myName>/add_order", methods=["POST"])
def add_order(myName):
    query = request.POST

    if "vessel" not in query:
        return select_err("Please provide a spacecraft myName as 'vessel' in query string.", query.html)

    if "authcode" not in query:
        return select_err("Please provide an authorization code as 'authcode' in query string.", query.html)

    auth = try_authorize_vessel(query.vessel, query.authcode)

    if type(auth) is Entity:
        vessel = auth
    else:
        return select_err(auth["msg"], query.html)

    if valid_json(query.order):
        order = json.loads(query.order)
        print(f"ORDER JSON: {order}")
    else:
        return select_err(f"Invalid JSON in order {query.order}", query.html)

    if order["time"] == None:
        order["time"] = datetime.datetime.now()
    elif "interval" in order:
        if type(order["interval"] in [int, float]):
            print(f"order time is at an interval of {order['time']} seconds")
            order["time"] = datetime.datetime.now(
            ) + datetime.timedelta(seconds=order["time"])
            print(f"set order time to {order['time'].isoformat()}")
        else:
            return {"ok": False, "msg": f"Invalid interval parameter: type must be int or float, not {type(order['interval'])}", "input": query.order}
    elif "time" in order:
        if valid_datetime(order["time"]):
            order["time"] = datetime.datetime.fromisoformat(order["time"])
        else:
            return {"ok": False, "msg": "Invalid time parameter.", "input": query.order}
    else:
        print("NO TIME SPECIFIED IN ORDER, SETTING TO CURRENT TIME")
        order["time"] = datetime.datetime.now()

    order["args"]["a"] = [float(e) for e in order["args"]["a"]]
    order_id = vessel.add_order(
        task=order["task"], args=order["args"], time=order["time"])
    update_to_now(myName)

    if "html" in query and valid_json(query.html) and json.loads(query.html):
        return f"Order <code>{order['task']} {order['args']} at {order['time']:%I:%M %p on %A, %b %d, %Y}</code> given to vessel {query.vessel} with order ID {order_id}."
    else:
        return {"ok": True, "vessel": query.vessel, "added_id": order_id}


@app.route("/game/<myName>/delete_order", methods=["POST"])
def delete_order(myName):
    # required keys: vessel, order_id, authcode
    # optional keys: html

    update_to_now(myName)
    query = request.POST

    print("in delete_order, headers =", dict(query))

    if not ("vessel" in query and "authcode" in query and "order_id" in query):
        return {"ok": False, "msg": "vessel, order_id, and authcode are required"}

    order_id = json.loads(query.order_id)
    try:
        order_id = int(order_id)
    except Exception as _:
        return {"ok": False, "msg": f"order_id must be an integer, but is {query.order_id}"}

    auth = try_authorize_vessel(query.vessel, query.authcode)

    if type(auth) is Entity:
        vessel = auth
    else:
        return auth

    if not Pending.query.filter_by(id=vessel.pending, order_id=order_id).first():
        return {"ok": False, "msg": f"no pending order #{query.order_id} for vessel {query.vessel}"}

    # all tests passed:
    vessel.clear_order(order_id)
    pending_count = len(vessel.get_pending())

    if "html" in query and bool(json.loads(query.html)):
        return {"ok": True, "removed_id": order_id, "pending_count": pending_count}
    else:
        return f"Removed order with ID {order_id} from vessel {query.vessel}. {pending_count} order(s) pending."

# @app.route("/game/<myName>/update_simulation_debug")


def update_to_now(myName=None):
    old = Game.query.first().server_time
    now = datetime.datetime.now()
    print("simulation will be updated from {} to {}, delta of {}".format(
        old, now, (now - old)), "...")

    temp_now = Game.query.first().server_time
    interval = now - temp_now
    if interval < datetime.timedelta(0):
        raise ValueError(
            f"Simulation attempting to time travel by {interval} seconds.")
    print("updating to", now, "with interval of", interval, "seconds")
    update(interval)

    new = Game.query.first().server_time

if os.path.exists("config.json"):
    with open("config.json", "r") as config_file:
        global_config = json.load(config_file)
else:
    global_config = {
        "server_addr": "localhost",
        "server_port": 5000,
        "static_dir": os.path.join("./", "static"),
        "debug": True,
        "welcome": "Welcome to the myrmidon/dotwar test server!"
    }

if __name__ == "__main__":
    print("[INFO] Starting dev server on", global_config["server_addr"], global_config["server_port"], "with debug",
          ["disabled", "enabled"][global_config["debug"]] + "...")
    app.run(host=global_config["server_addr"], port=global_config["server_port"],
        debug=global_config["debug"])
    
else:
    print("[INFO] Not in __main__, continuing with default_app only instantiated")
