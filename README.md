# netgen2odl

Script takes [NETGEN](https://github.com/rwestphal/netgen) YAML output and transforms it to [Opendaylights PCE Module REST API Calls](https://docs.opendaylight.org/en/stable-carbon/user-guide/pcep-user-guide.html)

Installation
------------

Create virtualenv and install `requirements.txt`.


Usage Example
-------------

`python3 netgen2odl.py example1.yaml rt1-xge4,rt4-xge` means it will create a route from router rt1 interface xge4 to router4-xge interface. It will validate the path input with the topology given by the yaml file (it will ungraceful crash if it cannot find a path in the yaml).

Todo
-----

- ~~Test against ODL~~
- Test with new examples from Renato
- ~~Run queries against ODL~~
- ~~Graceful error when path cannot be found~~
- Visualisation (current attempts with matplotlib are ugly)
- Add switch to delete all paths
