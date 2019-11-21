# netgen2odl

Script takes [NETGEN](https://github.com/rwestphal/netgen) YAML output and transforms it to [Opendaylights (ODL) PCE Module REST API Calls](https://docs.opendaylight.org/en/stable-carbon/user-guide/pcep-user-guide.html).
Also can list and delete existing records in ODL and request resyncronisation with the PCC.

Installation
------------

Clone this repository and `cd` into it. Create virtualenv activate it and install requirements. Make sure to use Python3.

```console
$ virtualenv n2oenv
$ source ./n2oenv/bin/activate
$ pip install -r requirements.txt
```

Help
-----

Run `python3 netgen2odl.py --help` for help.

