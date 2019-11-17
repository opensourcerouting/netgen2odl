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

parser = argparse.ArgumentParser(
    description="Takes network description in NETGEN's YAML format\
    and creates paths in an OpenDaylight PCEP instance")
parser.add_argument(
    'yamlfile',
    metavar='file.yaml',
    type=argparse.FileType('r'),
    help='The name of the YAML file')
parser.add_argument(
    'paths',
    metavar='hop1-ifname1,hop2-ifname2...hopn-ifnamen',
    nargs="+",
    type=str,
    help="Comma seperated lists with routernames-interfacenames along\
    the desired paths, multiple possible")
parser.add_argument(
    "--pcc",
    type=str,
    default="127.0.0.1", help="Specify IP address of PCC instance")
parser.add_argument(
    "--odl",
    type=str,
    default="http://admin:admin@localhost:8181/", help="Specify base URL of Open Day Light")

args = parser.parse_args()
yamlData = yaml.load(vars(args)['yamlfile'], Loader=yaml.Loader)
patharg = vars(args)['paths']
pccip = vars(args)['pcc']
odlurl = urlparse(vars(args)['odl'], allow_fragments=True)
paths = []
for path in patharg:
    hops = path.split(",")
    paths.append([ tuple(hop.split("-")) for hop in hops ])

yamlRouters = yamlData['routers']
graph = nx.Graph()
for (routername, routerdata) in yamlRouters.items():
    graph.add_node(routername)
    for (ifname, ifdata) in routerdata["links"].items():
        if ifname == "lo":
            continue
        interface = json.dumps({
            'name': ifname,
            'ipv4': ifdata['ipv4'],
            #'ipv6': ifdata['ipv6'],
            'mpls': ifdata['mpls']
        })
        destrouter = ifdata['peer'][0]
        destinterface = ifdata['peer'][1]

        # edge from router to interface:
        graph.add_edge(routername, routername+"-"+ifname)
        # add some metadata to the interface node
        graph[routername][routername+"-"+ifname]['name'] = ifname
        graph[routername][routername+"-"+ifname]['ipv4'] = ifdata['ipv4']
        try:
            graph[routername][routername+"-"+ifname]['ipv6'] = ifdata['ipv6']
        except KeyError:
            pass
        graph[routername][routername+"-"+ifname]['mpls'] = ifdata['mpls']
        # edge from source interface to dest interface:
        graph.add_edge(routername+"-"+ifname, destrouter+"-"+destinterface)

pathHop = []
for path in paths:
    currentPath =  []
    for (router, interface) in path:
        try:
            currentPath.append(graph[router][router+"-"+interface]['ipv4'])
        except KeyError:
            print("The past does not exist in the specified network topology")
            raise
    pathHop.append(currentPath)

toRequest = []
for path in pathHop:
    templatedata = {'hop':[ {'network': hop} for hop in path ]}
    templatedata['pccip'] = pccip
    templatedata['pathname'] = str(uuid.uuid4())[:8] # just a rundom 8 char word
    with open("add-lsp.xml.mustache", "r") as templatefile:
        templatestring = templatefile.read()
        parsedtemplate = pystache.parse(templatestring)
        renderer = pystache.Renderer()
        toRequest.append(renderer.render(parsedtemplate, templatedata))

class ODLError(Exception):
    """Raised for Errors dealing with OpenDayLight"""
    def __init__(self, message):
        self.message = message

def doRequest(urlObj, xmlstring):
    authstring = base64.b64encode(bytes("%s:%s" % (urlObj.username, urlObj.password), "ascii")).decode("ascii")
    httpHeader = {
        "Content-Type": "application/xml",
        "Authorization": "Basic %s" % authstring
    }
    urlObj = urlObj._replace(path="/restconf/operations/network-topology-pcep:add-lsp")
    urlObj = urlObj._replace(netloc=urlObj.hostname + ":" + str(urlObj.port))

    xmlbinary = xmlstring.encode('utf-8')
    urlstring = urlObj.geturl()
    req = urllib.request.Request(urlstring, data=xmlbinary, headers=httpHeader)
    with urllib.request.urlopen(req) as f:
        if f.getcode() == 204:
            return f.read().decode("utf-8")
        else:
            print(f.getcode())
            print(f.read().decode("utf-8"))
            raise ODLError("Is this path already registered in ODL?")

for xmlstring in toRequest:
    doRequest(odlurl, xmlstring)
