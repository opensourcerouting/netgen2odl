#!/usr/bin/env python3

import argparse
import yaml
import networkx as nx
import json
#import matplotlib.pyplot as plt
import pystache
import urllib.request
from urllib.parse import urlparse
import uuid
import base64
import sys
import os
import re
import ipaddress
import logging
import xml.etree.ElementTree as ET

def parse_args():
    parser = argparse.ArgumentParser(
        description="Takes network description in NETGEN's YAML format\
        and creates paths in an OpenDaylight PCEP instance")
    subparsers = parser.add_subparsers(
        help='modeswitch',
        dest='modeswitch',
        required=True)

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
    return srgb_start + srgb_offsetv4

def build_network(yamlRouters):
    graph = nx.Graph()
    for (routername, routerdata) in yamlRouters.items():
        graph.add_node(routername)
        sid = parse_isisd_config(routerdata["frr"]["isisd"]["config"])
        for (ifname, ifdata) in routerdata["links"].items():
            if ifname == "lo":
                continue

            destrouter = ifdata['peer'][0]
            destinterface = ifdata['peer'][1]

            # edge from router to interface:
            graph.add_edge(routername, routername+"/"+ifname)
            # add some metadata to the interface node
            graph[routername][routername+"/"+ifname]['name'] = ifname
            graph[routername][routername+"/"+ifname]['ipv4'] = ifdata['ipv4']
            graph[routername][routername+"/"+ifname]['sid'] = sid
            try:
                graph[routername][routername+"/"+ifname]['ipv6'] = ifdata['ipv6']
            except KeyError:
                pass
            graph[routername][routername+"/"+ifname]['mpls'] = ifdata['mpls']
            # edge from source interface to dest interface:
            graph.add_edge(routername+"/"+ifname, destrouter+"/"+destinterface)
    return graph

def create_path_in_network(graph, paths):
    pathHop = []
    for path in paths:
        print(path)
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

def create_xml(pathHop, args):
    pccip = args['pcc']
    toRequest = []
    for path in pathHop:
        templatedata = {'hop':[{'ip': hop, 'sid': sid} for hop, sid in path]}
        templatedata['pccip'] = pccip
        templatedata['pathname'] = str(uuid.uuid4())[:8] # just a rundom 8 char word
        templatedata['source-ipv4'] = pccip # ?!?
        templatedata['destination-ipv4'] = path[-1][0] # ip of last hop?!?
        with open(os.path.join(sys.path[0], "add-lsp.xml.mustache"), "r") as templatefile:
            templatestring = templatefile.read()
            parsedtemplate = pystache.parse(templatestring)
            renderer = pystache.Renderer()
            toRequest.append(renderer.render(parsedtemplate, templatedata))
    return toRequest

class ODLError(Exception):
    """Raised for Errors dealing with OpenDayLight"""
    def __init__(self, message):
        self.message = message

def do_request(args, xmlstring):
    odlUrlObj = urlparse(args['odl'], allow_fragments=True)
    authstring = base64.b64encode(
        bytes("%s:%s" % (odlUrlObj.username, odlUrlObj.password), "ascii")).decode("ascii")
    httpHeader = {
        "Content-Type": "application/xml",
        "Authorization": "Basic %s" % authstring
    }
    odlUrlObj = odlUrlObj._replace(path="/restconf/operations/network-topology-pcep:add-lsp")
    odlUrlObj = odlUrlObj._replace(netloc=odlUrlObj.hostname + ":" + str(odlUrlObj.port))

    xmlbinary = xmlstring.encode('utf-8')
    urlstring = odlUrlObj.geturl()
    req = urllib.request.Request(urlstring, data=xmlbinary, headers=httpHeader)
    logging.info("Sending request \n"+xmlstring+"\nto\n"+urlstring)
    with urllib.request.urlopen(req) as f:
        if f.getcode() == 204:
            return f.read().decode("utf-8")
        else:
            print(f.getcode())
            print(f.read().decode("utf-8"))
            raise ODLError("Is this path already registered in ODL?")

def do_requests(args, xmlstrings):
    for xmlstring in xmlstrings:
        do_request(args, xmlstring)

def get_odl_routes():
    '''Get all LSP routes inside ODL'''
def print_odl_routes(routes):
    '''Prints all routes to CLI'''

def flush_odl_routes(routes):
    '''Delete all existing routes in ODL LSP database'''

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

def add(args):
    network = build_network(yaml.load(args['yamlfile'], Loader=yaml.Loader)['routers'])
    hop_ips = create_path_in_network(network, parse_path_arg(args))
    xmlstrings = create_xml(hop_ips, args)
    do_requests(args, xmlstrings)

def flush_odl(args):
    print("FLUSH")

def sync_odl(args):
    print("Sync")
    pass

def list_odl(args):
    # url object
    # req object
    # parse: name, sender/endpoint, ero
    print("LIST")

args = parse_args()
if args["verbose"] > 0:
    logging.basicConfig(level=logging.INFO)
opts = {"add": add,
        "flush": flush_odl,
        "list": list_odl,
        "sync": sync_odl}
func = opts.get(args["modeswitch"], lambda: exit())
func(args)
