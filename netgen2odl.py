#!/usr/bin/env python3

import argparse
import yaml
import networkx as nx
import json
#import matplotlib.pyplot as plt
import pystache

parser = argparse.ArgumentParser(
    description="Takes network description in NETGEN's YAML format and creates paths in an OpenDaylight PCEP instance")
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
    help='Comma seperated lists with routernames-interfacenames along the desired paths, multiple possible')
args = parser.parse_args()
yamlData = yaml.load(vars(args)['yamlfile'], Loader=yaml.Loader)
patharg = vars(args)['paths']
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

path_hop = []
for path in paths:
    for (router, interface) in path:
        path_hop.append(graph[router][router+"-"+interface]['ipv4'])
nx.write_adjlist(graph, "asd")

templatedata = {'hop':[ {'network': hop} for hop in path_hop ]}
with open("add-lsp.xml.mustache", "r") as templatefile:
    templatestring = templatefile.read()
    parsedtemplate = pystache.parse(templatestring)
    renderer = pystache.Renderer()
    print(renderer.render(parsedtemplate, templatedata))

# plot the graph
#plt.subplot(121)
#nx.draw_spectral(graph, with_labels=True)
#plt.show()
