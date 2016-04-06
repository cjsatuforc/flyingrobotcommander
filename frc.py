#!/usr/bin/env python

"""
/*
 * Copyright (C) 2003-2016 The Paparazzi Team
 *
 * This file is part of paparazzi.
 *
 * paparazzi is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2, or (at your option)
 * any later version.
 *
 * paparazzi is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with paparazzi; see the file COPYING.  If not, see
 * <http://www.gnu.org/licenses/>.
 */
"""

from __future__ import print_function
import sys
from os import path, getenv
import os
import time
import argparse
from flask import Flask, request, Response, render_template
import json

# if PAPARAZZI_SRC not set, then assume the tree containing this file is a reasonable substitute
PPRZ_SRC = getenv("PAPARAZZI_SRC", path.normpath(path.join(path.dirname(path.abspath(__file__)), '~/paparazzi/')))

sys.path.append(PPRZ_SRC + "/sw/lib/python")
sys.path.append(PPRZ_SRC + "/sw/ext/pprzlink/lib/v1.0/python")

from ivy_msg_interface  import IvyMessagesInterface
from pprzlink.message   import PprzMessage
from settings_xml_parse import PaparazziACSettings

from math import radians


app = Flask(__name__)

# --- Configure server logging levels

# Only spit out error level server messages
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


# --- Class/Global state variables

ivy_interface = IvyMessagesInterface("FlyingRobotCommander", start_ivy=False)
frc_version   = "0.2.2"
verbose       = 0              # Default is disabled(i.e. = 0)
curl          = 0              # Default is disabled(i.e. = 0)
subscribe     = 0              # Default is disabled(i.e. = 0)
server_host   = "127.0.0.1"    # Default to local host)
server_port   = 5000           # Default it flask port)


# --- Aircraft, Flighblock & Message related state/methods

class Message(PprzMessage):
    def __init__(self, class_name, name, msg):
        super(Message, self).__init__(class_name, name)
        self.field_controls = {}
        self.index          = None
        self.last_seen      = time.clock()
        self.latest_msg     = msg

class Waypoint(object):
    def __init__(self, wp_id, wp_name, wp_x, wp_y):
        self.wp_id   = wp_id
        self.wp_name = wp_name
        self.wp_x    = wp_x
        self.wp_y    = wp_y        

class Flightblock(object):
    def __init__(self, fb_id, fb_name):
        self.fb_id   = fb_id
        self.fb_name = fb_name

class Aircraft(object):
    def __init__(self, ac_id, name, color):
        self.ac_id        = ac_id
        self.name         = name
        self.color        = color
        self.flightblocks = {}
        self.waypoints    = {}
        self.messages     = {}

aircrafts = {}

def add_new_aircraft_message( aircraft, msg_class, name, msg):
    aircraft.messages[name] = Message(msg_class, name, msg)

def add_new_aircraft_waypoint( aircraft, wp_id, wp_name, wp_x, wp_y):
    aircraft.waypoints[wp_id] = Waypoint(wp_id, wp_name, wp_x, wp_y)      

def add_new_aircraft_flightblock( aircraft, fb_id, fb_name):
    aircraft.flightblocks[fb_id] = Flightblock(fb_id, fb_name)      

def add_new_aircraft(ac_id, name, color):
    aircrafts[ac_id] = Aircraft(ac_id, name, color)

def print_aircraft_data():
    for ac_id in aircrafts:
        print( ac_id, aircrafts[ac_id].name, aircrafts[ac_id].color )
        for wp_id in aircrafts[ac_id].waypoints:
            print( wp_id, aircrafts[ac_id].waypoints[wp_id].wp_name, aircrafts[ac_id].waypoints[wp_id].wp_x, aircrafts[ac_id].waypoints[wp_id].wp_y )
        for fb_id in aircrafts[ac_id].flightblocks:
            print( fb_id, aircrafts[ac_id].flightblocks[fb_id].fb_name )

aircraft_client_list       = []   # Used for rows in client; preserve list order
flightblock_client_list    = []   # Used for columns in client view for flightblocks; preserve list order
waypoint_client_list       = []   # Used for columns in client view for waypoints; preserve list order


# --- Helper methods ---

def print_curl_header(host, port):
    print('#!/bin/bash')
    print('host=%s' % host)
    print('port=%s' % port)

def print_curl_format():
    #print('curl %s' % request.url)
    print('curl http://$host:$port%s' % request.path)

def print_ivy_trace(msg):
    print("Sending ivy message interface: %s" % msg)

import xml.etree.cElementTree as ET    
PPRZ_SRC_CONF = os.path.join(PPRZ_SRC, "conf")

def static_init_client_configuration_data(fname):
    tree = ET.parse(fname)
    root = tree.getroot()
    tmp_ac_id = 0   # Assuming all aircraft use the same flight plan, we cache an aircraft index

    # Populate aircraft client objects
    for aircraft in root.findall('aircraft'):
        ac_id = int(aircraft.get('ac_id')) if aircraft.get('ac_id') else None
        name  = aircraft.get('name')
        if name: 
            ac_id = next((idx for idx in aircrafts if aircrafts[idx].name == name), None)
            #print("Found aircraft name: %s" % aircrafts[ac_id].name)
        color = aircraft.get('color')
        if color:  #Override current conf.xml gui_color value if defined in frc_conf.xml
            aircrafts[ac_id].color = color
        aircraft_client_add(ac_id)
        tmp_ac_id = ac_id  # Cache the current aircraft index for use in flightblock and waypoint search

    # Populate flightblock client objects
    for flightblock in root.findall('flightblock'):
        fb_id = int(flightblock.get('fb_id')) if flightblock.get('fb_id') else None
        name = flightblock.get('name')
        if name: 
            fb_id = next((idx for idx in aircrafts[tmp_ac_id].flightblocks if aircrafts[tmp_ac_id].flightblocks[idx].fb_name == name), None)
            #print("Found flightblock name: %s" % aircrafts[tmp_ac_id].flightblocks[fb_id].fb_name)
        flightblock_client_add(fb_id)

    # Populate waypoint client objects
    for waypoint in root.findall('waypoint'):
        wp_id = int(waypoint.get('wp_id')) if waypoint.get('wp_id') else None
        name = waypoint.get('name')
        if name: 
            wp_id = next((idx for idx in aircrafts[tmp_ac_id].waypoints if aircrafts[tmp_ac_id].waypoints[idx].wp_name == name), None)
            #print("Found waypoint name: %s" % aircrafts[tmp_ac_id].waypoints[wp_id].wp_name)
        waypoint_client_add(wp_id)
    

def static_init_configuration_data():
    tree = ET.parse(os.path.join( PPRZ_SRC_CONF, 'conf.xml' ))
    root = tree.getroot()

    # Populate aircraft objects
    for aircraft in root.findall('aircraft'):
        acid           = int(aircraft.get('ac_id'))
        name           = aircraft.get('name')
        flightplanpath = aircraft.get('flight_plan')
        #airframepath   = aircraft.get('airframe')
        color          = aircraft.get('gui_color')
        add_new_aircraft(acid, name, color)
        aircraft = aircrafts[acid]
    
        # Populate flight plan objects
        fptree = ET.parse(os.path.join( PPRZ_SRC_CONF, flightplanpath )) 
        fproot = fptree.getroot()
        # Process waypoints
        # Populate WP_dummy, idx=0, x="42.0" and y="42.0"; note: used values defined in gen_flight_plan.ml
        add_new_aircraft_waypoint(aircraft, 0, "dummy", "42.0", "42.0")
        # Waypoints indexes are adjusted by 1 to account for dummy waypoint above
        for idx, waypoint in enumerate(fproot.iter('waypoint')):
            name = waypoint.get('name')
            px   = waypoint.get('x')
            py   = waypoint.get('y')
            add_new_aircraft_waypoint(aircraft, idx+1, name, px, py)
        # Process flightblocks
        for idx, block in enumerate(fproot.iter('block')):
            name = block.get('name')
            add_new_aircraft_flightblock(aircraft, idx, name)


def callback_aircraft_messages(ac_id, msg):
    # Possibly add the aircraft to the list
    if ac_id not in aircrafts:
        add_new_aircraft(ac_id, 'unknown', 'unknown')
    aircraft = aircrafts[ac_id]
    # Add the messages and say when last seen
    add_new_aircraft_message(aircraft, msg.msg_class, msg.name,msg)
    aircrafts[ac_id].messages[msg.name].last_seen = time.time()
    for index in range(0, len(msg.fieldvalues)):
        aircraft.messages[msg.name].field_controls[index]=msg.get_field(index)


# --- Routes/Paths ----

@app.route('/')
def index():
    retval = 'Flying Robot Commander Server Running....'

    if verbose: 
        retval = 'Flying Robot Commander Server Running....\n'
    if curl: print_curl_format()
    return retval


@app.route('/aircraft/')
def aircraft_all():
    aclist = []
    for ac_id in aircrafts:
        aclist.append(ac_id)
    if curl: print_curl_format()
    return str(aclist)


@app.route('/aircraft/<int:ac_id>')
def aircraft(ac_id):
    ac_id = int(ac_id)
    if ac_id in aircrafts:
        alist = []
        alist.append(ac_id)
        alist.append(aircrafts[ac_id].name)
        alist.append(aircrafts[ac_id].color)
        for wp_id in aircrafts[ac_id].waypoints:
            alist.append(wp_id)   
            alist.append(aircrafts[ac_id].waypoints[wp_id].wp_name)   
            alist.append(aircrafts[ac_id].waypoints[wp_id].wp_x)   
            alist.append(aircrafts[ac_id].waypoints[wp_id].wp_y)   
        for fb_id in aircrafts[ac_id].flightblocks:
            alist.append(fb_id)   
            alist.append(aircrafts[ac_id].flightblocks[fb_id].fb_name)   
        if curl: print_curl_format()
        return str(alist)    
    return "unknown id"    


@app.route('/aircraft/client/')
def aircraft_client_all():
    if curl: print_curl_format()
    return str(aircraft_client_list)


@app.route('/aircraft/client/add/<int:ac_id>')
def aircraft_client_add(ac_id):
    ac_id = int(ac_id)
    if ac_id in aircrafts:
        if ac_id not in aircraft_client_list:       
            aircraft_client_list.append(ac_id)
        if curl: print_curl_format()
        return str(aircraft_client_list)    
    return "unknown aircraft id"    


@app.route('/flightblock/noop/')
def flightblock_noop():
    if curl: print_curl_format()
    return "noop"


@app.route('/flightblock/client/')
def flightblock_client_all():
    if curl: print_curl_format()
    return str(flightblock_client_list)


@app.route('/flightblock/client/add/<int:fb_id>')
def flightblock_client_add(fb_id):
    if aircraft_client_list:
        fb_id = int(fb_id)
        if fb_id in aircrafts[aircraft_client_list[0]].flightblocks:  # KLUDGE: Use first defined aircraft's flightblocks to verify, assume all aircraft use same flight plan
            if fb_id not in flightblock_client_list:       
                flightblock_client_list.append(fb_id)
            if curl: print_curl_format()
            return str(flightblock_client_list)    
        return "unknown flightblock id"
    return "aircraft list is empty"    


@app.route('/waypoint/client/')
def waypoint_client_all():
    if curl: print_curl_format()
    return str(waypoint_client_list)


@app.route('/waypoint/client/add/<int:wp_id>')
def waypoint_client_add(wp_id):
    if aircraft_client_list:
        wp_id = int(wp_id)
        if wp_id in aircrafts[aircraft_client_list[0]].waypoints:  # KLUDGE: Use first defined aircraft's waypoints to verify, assume all aircraft use same flight plan
            if wp_id not in waypoint_client_list:       
                waypoint_client_list.append(wp_id)
            if curl: print_curl_format()
            return str(waypoint_client_list)    
        return "unknown waypoint id"
    return "aircraft list is empty"    


@app.route('/message/<int:ac_id>')
def message(ac_id):
    ac_id = int(ac_id)
    if ac_id in aircrafts:
        messagelist = []
        for key in aircrafts[ac_id].messages:
            messagelist.append(key)
        if curl: print_curl_format()
        return str(json.dumps(messagelist))
    return "unknown id"    


@app.route('/message/<int:ac_id>/<messagename>')
def message_byname(ac_id, messagename):
    # If the message is valid, return the latest message
    ac_id = int(ac_id)
    if ac_id in aircrafts:
        if messagename in aircrafts[ac_id].messages:
            if curl: print_curl_format()
            return Response(str(aircrafts[ac_id].messages[messagename].latest_msg.to_json()))
        else:
            return "unknown message"
    else:
        return "unknown id"


@app.route('/guidance/')
def guidance_all():
    retval = ''

    if verbose: 
        retval = 'Guidance: All\n'
    if curl: print_curl_format()
    return retval


#Set auto2 mode to GUIDED(value=19) or NAV(value=13).
@app.route('/guidance/setmode/<int:value>')
def guidance_setmode_all_aircraft(value):
    retval = ''

    for ac_id in aircraft_client_list:
        try:
            settings = PaparazziACSettings(ac_id)
        except Exception as e:
            print(e)
            return
        try:
            index = settings.name_lookup['auto2'].index
        except Exception as e:
            print(e)
            print("auto2 setting not found, mode change not possible.")
            return

        if index is not None:
            msg = PprzMessage("ground", "DL_SETTING")
            msg['ac_id'] = ac_id
            msg['index'] = index
            msg['value'] = value
            if verbose: 
                print_ivy_trace(msg)
                retval = 'Guidance Mode All Aircraft: index=%d, value=%d\n' % (index, value)
            ivy_interface.send(msg)
    if curl: print_curl_format()
    return retval


#Set auto2 mode to GUIDED(value=19) or NAV(value=13).
@app.route('/guidance/setmode/<int:ac_id>/<int:value>')
def guidance_setmode(ac_id, value):
    retval = ''

    try:
        settings = PaparazziACSettings(ac_id)
    except Exception as e:
        print(e)
        return
    try:
        index = settings.name_lookup['auto2'].index
    except Exception as e:
        print(e)
        print("auto2 setting not found, mode change not possible.")
        return

    if index is not None:
        msg = PprzMessage("ground", "DL_SETTING")
        msg['ac_id'] = ac_id
        msg['index'] = index
        msg['value'] = value
        if verbose: 
            print_ivy_trace(msg)
            retval = 'Guidance mode: ac_id=%d, index=%d, value=%d\n' % (ac_id, index, value)
        ivy_interface.send(msg)
        if curl: print_curl_format()
        return retval


@app.route('/guidance/<int:flag>/<x>/<y>/<z>/<yaw>')
def guidance_all_aircraft(flag, x, y, z, yaw):
    retval = ''

    for ac_id in aircraft_client_list:
        msg = PprzMessage("datalink", "GUIDED_SETPOINT_NED")
        msg['ac_id'] = ac_id
        msg['flags'] = flag
        msg['x']     = x
        msg['y']     = y
        msg['z']     = z
        msg['yaw']   = yaw
        if verbose: 
            print_ivy_trace(msg)
            retval = 'Guidance All Aircraft: flag=%d, x=%s, y=%s, z=%s, yaw=%s\n' % (flag, x, y, z, yaw)
        ivy_interface.send_raw_datalink(msg)
    if curl: print_curl_format()
    return retval


@app.route('/guidance/<int:ac_id>/<int:flag>/<x>/<y>/<z>/<yaw>')
def guidance(ac_id, flag, x, y, z, yaw):
    retval = ''

    msg = PprzMessage("datalink", "GUIDED_SETPOINT_NED")
    msg['ac_id'] = ac_id
    msg['flags'] = flag
    msg['x']     = x
    msg['y']     = y
    msg['z']     = z
    msg['yaw']   = yaw
    if verbose: 
        print_ivy_trace(msg)
        retval = 'Guidance: ac_id=%d, flag=%d, x=%s, y=%s, z=%s, yaw=%s\n' % (ac_id, flag, x, y, z, yaw)
    ivy_interface.send_raw_datalink(msg)
    if curl: print_curl_format()
    return retval


@app.route('/waypoint/')
def waypoint_all():
    retval = ''

    if verbose: 
        retval = 'Waypoint: All\n'
    if curl: print_curl_format()
    return retval


@app.route('/waypoint/<int:wp_id>/<lat>/<lon>/<alt>')
def waypoint_all_aircraft(wp_id, lat, lon, alt):
    retval = ''

    for ac_id in aircraft_client_list:
        msg = PprzMessage("ground", "MOVE_WAYPOINT")
        msg['ac_id'] = ac_id
        msg['wp_id'] = wp_id
        msg['lat']   = lat
        msg['long']  = lon
        msg['alt']   = alt
        if verbose: 
            print_ivy_trace(msg)
            retval = 'Waypoint All Aircraft: wp_id=%d, lat=%s, lon=%s, alt=%s\n' % (wp_id, lat, lon, alt)
        ivy_interface.send(msg)
    if curl: print_curl_format()
    return retval


@app.route('/waypoint/<int:ac_id>/<int:wp_id>/<lat>/<lon>/<alt>')
def waypoint(ac_id, wp_id, lat, lon, alt):
    retval = ''

    msg = PprzMessage("ground", "MOVE_WAYPOINT")
    msg['ac_id'] = ac_id
    msg['wp_id'] = wp_id
    msg['lat']   = lat
    msg['long']  = lon
    msg['alt']   = alt
    if verbose: 
        print_ivy_trace(msg)
        retval = 'Waypoint: ac_id=%d, wp_id=%d, lat=%s, lon=%s, alt=%s\n' % (ac_id, wp_id, lat, lon, alt)
    ivy_interface.send(msg)
    if curl: print_curl_format()
    return retval


@app.route('/flightblock/<int:fb_id>')
def flightblock_all_aircraft(fb_id):
    retval = ''

    for ac_id in aircraft_client_list:
        msg = PprzMessage("ground", "JUMP_TO_BLOCK")
        msg['ac_id']    = ac_id
        msg['block_id'] = fb_id
        if verbose: 
            print_ivy_trace(msg)
            retval = 'Flightblock All Aircraft: fb_id=%d\n' % (fb_id)
        ivy_interface.send(msg)
    if curl: print_curl_format()
    return retval


@app.route('/flightblock/<int:ac_id>/<int:fb_id>')
def flightblock(ac_id, fb_id):
    retval = ''

    msg = PprzMessage("ground", "JUMP_TO_BLOCK")
    msg['ac_id']    = ac_id
    msg['block_id'] = fb_id
    if verbose: 
        print_ivy_trace(msg)
        retval = 'Flightblock: ac_id=%d, fb_id=%d\n' % (ac_id, fb_id)
    ivy_interface.send(msg)
    if curl: print_curl_format()
    return retval


@app.route('/show/flightblock/')
def showflightblock():
    return render_template('flightblock.html', p_host=server_host, p_port=server_port, 
                            p_row_count=len(aircraft_client_list), p_row_list=aircraft_client_list, 
                            p_col_count=len(flightblock_client_list), p_col_list=flightblock_client_list)


@app.route('/show/guided/')
def showguided():
    return render_template('guided.html', p_host=server_host, p_port=server_port, 
                            p_row_count=len(aircraft_client_list), p_row_list=aircraft_client_list,
                            p_col_count=10) 


@app.route('/show/waypoint/')
def showwaypoint():
    return render_template('waypoint.html', p_host=server_host, p_port=server_port, 
                            p_row_count=len(aircraft_client_list), p_row_list=aircraft_client_list,
                            p_col_count=len(waypoint_client_list), p_col_list=waypoint_client_list) 


@app.route('/show/waypointhover/')
def showwaypointhover():
    return render_template('waypointhover.html', p_host=server_host, p_port=server_port, 
                            p_row_count=len(aircraft_client_list), p_row_list=aircraft_client_list,
                            p_col_count=8) 



@app.route('/about')
def about():
    return 'About: Flying Robot Commander Server v%s\n' % (frc_version)


# --- Main body ----
if __name__ == '__main__':

    # Get/set the required IP address and port number along with other command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--ip", type=str, default="127.0.0.1",
                        help="ip address")
    parser.add_argument("-p","--port", type=int, default=5000,
                        help="port number")
    parser.add_argument("-f","--file", type=str, default="frc_conf.xml",
                        help="client configuration file")
    parser.add_argument("-c","--curl",      action="store_true", help="dump actions as curl commands")
    parser.add_argument("-s","--subscribe", action="store_true", help="subscribe to the ivy bus")
    parser.add_argument("-v","--verbose",   action="store_true", help="verbose mode")

    try:
        # --- Startup state initialization block
        args = parser.parse_args()
        static_init_configuration_data()
        static_init_client_configuration_data(args.file)
        if args.verbose: 
            print_aircraft_data()
        if args.subscribe: 
            ivy_interface.subscribe(callback_aircraft_messages)
        ivy_interface.start()

        # Handle misc. command line arguments
        if args.verbose: 
            verbose=args.verbose
        if args.curl: 
            curl=args.curl
            print_curl_header(args.ip, args.port)

        # --- Main loop
        # Supply flask the appropriate ip address and port for the server
        server_host = args.ip      # Store for use in htlm template generation
        server_port = args.port    # Store for use in htlm template generation
        app.run(host=args.ip,port=args.port,threaded=True)

        # --- Shutdown state block
        ivy_interface.shutdown()

    except Exception as e:
        print(e)
        sys.exit(0)
