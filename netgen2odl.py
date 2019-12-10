#!/usr/bin/env python3

import argparse
import yaml
import networkx as nx
import json
#import matplotlib.pyplot as plt
import pystache
import urllib.request
import urllib
from urllib.parse import urlparse
import uuid
import base64
import sys
import os
import re
import ipaddress
import logging
import xml.etree.ElementTree as ET
import http


def parse_args():
    parser = argparse.ArgumentParser(
        description="Takes network description in NETGEN's YAML format\
        and creates, deletes, lists paths in an OpenDaylight PCEP instance. \
        Also can request PCC resync.")
    subparsers = parser.add_subparsers(
        help='modeswitch',
        dest='modeswitch'
    )

    #parentParser = argparse.ArgumentParser()
    parser.add_argument(
        "--pcc",
        type=str,
        default="127.0.0.1",
        help="Specify IP address of PCC instance. Defaults to localhost. Default %(default)s")
    parser.add_argument(
        "--odl",
        type=str,
        default="http://admin:admin@localhost:8181/",
        help="Specify base URL of Open Day Light. Default %(default)s")
    parser.add_argument(
        "--verbose", "-v",
        default = 0,
        action="count")

    addParser = subparsers.add_parser("add")
    addParser.add_argument(
        'yamlfile',
        metavar='file.yaml',
        type=argparse.FileType('r'),
        help='The name of the YAML file')
    addParser.add_argument(
        'paths',
        metavar='hop1/ifname1,hop2/ifname2...hopn/ifnamen',
        nargs="+",
        type=str,
        help="Comma seperated lists with routernames-interfacenames along\
        the desired paths, multiple possible")

    updateParser = subparsers.add_parser("update", parents=[addParser], add_help=False)
    updateParser.add_argument(
        'pathname',
        metavar='pathname_to_replace',
        type=str,
        help="The name of the path to replace"
    )

    flushParser = subparsers.add_parser("flush")

    listParser = subparsers.add_parser("list")

    syncParser = subparsers.add_parser("sync")


    return vars(parser.parse_args())

def parse_isisd_config(config):
    lines = config.split("\n")
    srgb_start = None
    srgb_offsetv4 = None
    srgb_offsetv6 = None
    for line in lines:
        if line.startswith(" segment-routing global-block "):
            items = line.split(" ")
            srgb_start = int(items[3])
        if line.startswith(" segment-routing prefix "):
            items = line.split(" ")
            parsed_network = ipaddress.ip_network(items[3])
            if isinstance(parsed_network, ipaddress.IPv4Network):
                srgb_offsetv4 = int(items[5])
            elif isinstance(parsed_network, ipaddress.IPv6Network):
                srgb_offsetv6= int(items[5])
    if srgb_start == None:
    	return None
    return srgb_start + srgb_offsetv4

def build_network(yamlData):
    graph = nx.Graph()
    busses = {}
    for (switchname, switchdata) in yamlData['switches'].items():
        busses[switchname] = []
        for (linkname, linkdata) in switchdata['links'].items():
            busses[switchname].append(linkdata['peer'])
    print(busses)
    for (routername, routerdata) in yamlData['routers'].items():
        graph.add_node(routername)
        if "isisd" in routerdata["frr"]:
        	sid = parse_isisd_config(routerdata["frr"]["isisd"]["config"])
        else:
        	sid = None
        ipv4 = ""
        for (ifname, ifdata) in routerdata["links"].items():
            if ifname == "lo":
                ipv4 = ifdata['ipv4'][:-3]
                continue

            destrouter = ifdata['peer'][0]
            destinterface = ifdata['peer'][1]

            # edge from router to interface:
            graph.add_edge(routername, routername+"/"+ifname)
            # add some metadata to the interface node
            graph[routername][routername+"/"+ifname]['name'] = ifname
            graph[routername][routername+"/"+ifname]['ipv4'] = ipv4 #ifdata['ipv4']
            graph[routername][routername+"/"+ifname]['sid'] = sid
            try:
                graph[routername][routername+"/"+ifname]['ipv6'] = ifdata['ipv6']
            except KeyError:
                pass
            graph[routername][routername+"/"+ifname]['mpls'] = ifdata['mpls']
            # edge from source interface to dest interface:
            # if switch, add all connected on that bus
            if destrouter in busses:
                for peer in busses[destrouter]:
                    srcedge = routername
                    dstedge = peer[0]
                    if srcedge == dstedge:
                        continue
                    graph.add_edge(routername+"/"+ifname, peer[0] + "/" + peer[1])
            else:
                graph.add_edge(routername + "/" + ifname, destrouter + "/" + destinterface)
    return graph

def create_path_in_network(graph, paths):
    pathHop = []
    for path in paths:
        currentPath = []
        for (router, interface) in path[:-1]:
            try:
                currentPath.append(
                    (graph[router][router+"/"+interface]['ipv4'],
                     graph[router][router+"/"+interface]["sid"]))
            except KeyError:
                print("The past does not exist in the specified network topology")
                raise
#        try:
#            last_router = path[-1]
#            currentPath.append(graph[last_router])
#        except KeyError:
#            print("Last router could not be found")
#            raise
        pathHop.append(currentPath)
    return pathHop

def render_template(filename, data):
    with open(os.path.join(sys.path[0], filename), "r") as templatefile:
        templatestring = templatefile.read()
        parsedtemplate = pystache.parse(templatestring)
        renderer = pystache.Renderer()
        return renderer.render(parsedtemplate, data)

def create_xml(pathHop, args, pathname=None):
    pccip = args['pcc']
    toRequest = []
    for path in pathHop:
        templatedata = {'hop':[{'ip': hop, 'sid': sid} for hop, sid in path[1:]]}
        templatedata['pccip'] = pccip
        templatedata['source-ipv4'] = path[0][0]
        templatedata['destination-ipv4'] = path[-1][0] # ip of last hop?!?
        if pathname is None:
            templatedata['pathname'] = str(uuid.uuid4())[:8] # just a rundom 8 char word
            toRequest.append(render_template("add-lsp.xml.mustache", templatedata))
        else:
            templatedata['pathname'] = pathname
            toRequest.append(render_template("update-lsp.xml.mustache", templatedata))

        toRequest.append(render_template("add-lsp.xml.mustache", templatedata))
    return toRequest

class ODLError(Exception):
    """Raised for Errors dealing with OpenDayLight"""
    def __init__(self, message):
        self.message = message

def odl_request(args, apipath, xmlstring=None):
    odlUrlObj = urlparse(args['odl'], allow_fragments=True)
    authstring = base64.b64encode(
        bytes("%s:%s" % (odlUrlObj.username, odlUrlObj.password), "ascii")).decode("ascii")
    httpHeader = {
        "Accept": "application/xml",
        "Content-Type": "application/xml",
        "Authorization": "Basic %s" % authstring
    }
    odlUrlObj = odlUrlObj._replace(path=apipath)
    odlUrlObj = odlUrlObj._replace(netloc=odlUrlObj.hostname + ":" + str(odlUrlObj.port))

    urlstring = odlUrlObj.geturl()
    if xmlstring is not None:
        xmlbinary = xmlstring.encode('utf-8')
        req = urllib.request.Request(urlstring, data=xmlbinary, headers=httpHeader)
        logging.info("Request \n" + xmlstring + "\nto\n" + urlstring)
    else:
        req = urllib.request.Request(urlstring, headers=httpHeader)
        logging.info("Request to " + urlstring + "With headers: " + str(httpHeader))

    return req

def do_add_request(args, xmlstring):
    req = odl_request(args,
                      "/restconf/operations/network-topology-pcep:add-lsp",
                      xmlstring)
    try:
        with urllib.request.urlopen(req) as f:
            if f.getcode() == 204 or f.getcode() == 200:
                return f.read().decode("utf-8")
            else:
                print(f.getcode())
                print(f.read().decode("utf-8"))
                raise ODLError("Got unexpected reply from ODL")
    except urllib.error.HTTPError as e:
        logging.error(str(e.code) + " " + str(e.reason) + " " + str(e.headers) + e.read().decode("utf-8"))
        raise

def do_add_requests(args, xmlstrings):
    for xmlstring in xmlstrings:
        do_add_request(args, xmlstring)

def do_update_request(args, xmlstring):
    req = odl_request(args,
                      "/restconf/operations/network-topology-pcep:update-lsp",
                      xmlstring)
    with urllib.request.urlopen(req) as f:
        if f.getcode() == 204:
            return f.read().decode("utf-8")
        else:
            print(f.getcode())
            print(f.read().decode("utf-8"))
            raise ODLError("Is this path already registered in ODL?")

def do_update_requests(args, xmlstrings):
    for xmlstring in xmlstrings:
        do_update_request(args, xmlstring)

class PathParsingError(Exception):
    """Raised when path does not follow the requirements"""
    def __init__(self, message):
        self.message = message

def parse_path_arg(args):
    patharg = args['paths']
    paths = []
    for path in patharg:
        hop_chunks = path.split(",")
        if len(hop_chunks) < 2:
            raise PathParsingError("Paths with only one hop not allowed")
        hop = []
        for hop_string in hop_chunks[:-1]:
            router_interface = hop_string.split("/")
            if len(router_interface) != 2:
                raise PathParsingError("Format is router/interface")
            else:
                hop.append(tuple(router_interface))
        hop.append(hop_chunks[-1])
        paths.append(hop)
    return paths

def get_all_lsp_routes(args):
    enquoted_dest = urllib.parse.quote("pcc://" + args["pcc"], safe=":")
    req = odl_request(args,
                      "/restconf/operational/network-topology:network-topology/topology/pcep-topology/node/"+enquoted_dest)
    logging.info(str(req.full_url) + str(req.header_items()) + str(req.data) + str(req.type) + str(req.host))
    try:
        with urllib.request.urlopen(req) as f:
            if f.getcode() == 200:
                res = f.read().decode("utf-8")
                xml = ET.fromstring(res)
                paths = []
                xml_pccs = xml.findall("./{urn:opendaylight:params:xml:ns:yang:topology:pcep}path-computation-client")
                for pcc in xml_pccs:
                    routes = pcc.findall("{urn:opendaylight:params:xml:ns:yang:topology:pcep}reported-lsp")
                    for route in routes:
                        routename = (route.find("{urn:opendaylight:params:xml:ns:yang:topology:pcep}name").text)
                        paths.append({"name": routename})
                return paths
            else:
                raise ODLError("API did not give a topology list")
    except urllib.error.HTTPError as e:
        logging.error(str(e.code) + " " + str(e.reason) + " " + str(e.headers))
        raise

def add(args):
    """Add a route to ODL'S LSP database"""
    network = build_network(yaml.load(args['yamlfile'], Loader=yaml.Loader))
    hop_ips = create_path_in_network(network, parse_path_arg(args))
    xmlstrings = create_xml(hop_ips, args)
    do_add_requests(args, xmlstrings)

def flush_odl(args):
    '''Delete all existing routes in ODL LSP database'''
    routes = get_all_lsp_routes(args)
    print("Found " + str(len(routes)) + " in ODL")
    print("Sending deletion requests....")
    for route in routes:
        xmlstring = render_template("delete-lsp.xml.mustache",
                                    {"pccip": args["pcc"],
                                     "tunnel-name": route["name"]
                                    })
        req = odl_request(args,
                          "/restconf/operations/network-topology-pcep:remove-lsp",
                          xmlstring)
        with urllib.request.urlopen(req) as f:
            if f.getcode() == 204:
                print("Deleted " + str(route))
            else:
                raise ODLError("Deletion failed. HTTP " +
                               str(f.getcode()) +
                               f.read().decode("utf-8"))

def sync_odl(args):
    """Send request to sync ODL'S LSP with the PCC"""
    xmlstring = render_template("sync-lsp.xml.mustache",
                                {"pccip": args["pcc"]})
    req = odl_request(args,
                       "/restconf/operations/network-topology-pcep:trigger-sync",
                       xmlstring)
    with urllib.request.urlopen(req) as f:
        if f.getcode() == 200:
            print("Sync request sent. HTTP " + str(f.getcode()) + f.read().decode("utf-8"))
        else:
            raise ODLError("Sync failed. HTTP" + str(f.getcode()) + f.read().decode("utf-8"))

def list_odl(args):
    '''Get all LSP routes inside ODL'''
    routes = get_all_lsp_routes(args)
    print(routes)

def update_odl(args):
    """Update a LSP route"""
    network = build_network(yaml.load(args['yamlfile'], Loader=yaml.Loader))
    hop_ips = create_path_in_network(network, parse_path_arg(args))
    xmlstrings = create_xml(hop_ips, args, args["pathname"])[0]
    do_update_request(args, xmlstrings)

args = parse_args()
if args["verbose"] > 0:
    logging.basicConfig(level=logging.INFO)
opts = {"add": add,
        "flush": flush_odl,
        "list": list_odl,
        "sync": sync_odl,
        "update": update_odl}
func = opts.get(args["modeswitch"], lambda: exit())
func(args)
