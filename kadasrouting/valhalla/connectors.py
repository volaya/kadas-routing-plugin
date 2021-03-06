import os
import subprocess
import logging
import requests
import json
from jinja2 import Environment, FileSystemLoader

from kadasrouting.exceptions import Valhalla400Exception
from kadasrouting.utilities import localeName
from qgis.core import QgsSettings

LOG = logging.getLogger(__name__)


class Connector:

    def isAvailable(self):
        return True

    def prepareRouteParameters(self, points, profile="auto", options=None):
        options = options or {}
        locale_name = localeName()
        params = dict(
            costing=profile,
            show_locations=True,
            locations=points,
            directions_options={"language": locale_name})
        if options:
            params["costing_options"] = {profile: options}

        return params

    def prepareIsochronesParameters(self, points, profile, options, intervals, colors):
        # build contour json
        if len(intervals) != len(colors):
            LOG.warning(self.tr(
                "The number of intervals and colors are different, using default color"
            ))
            contours = [{"time": x} for x in intervals]
        else:
            contours = []
            for i in range(0, len(intervals)):
                contours.append({"time": intervals[i], "color": colors[i]})
        params = dict(
            costing=profile, locations=points, polygons=True,
            contours=contours, costing_options={profile: options})
        return params

    def prepareMapmatchingParameters(self, shape, profile, options):
        return {"shape": shape,
                "shape_match": "map_snap",
                "costing": profile,
                "costing_options": {profile: options}}


class ConsoleConnector(Connector):

    def isAvailable(self):
        return os.path.exists(self._valhallaExecutablePath())

    def _appDataDir(self):
        folder = os.path.expandvars('%APPDATA%\\KADAS\\routing\\')
        if not os.path.exists(folder):
            os.makedirs(folder)
        return folder

    def createMapmatchingParametersFile(self, params):
        outputFileName = os.path.join(self._appDataDir(), 'params.json')
        with open(outputFileName, 'w') as f:
            json.dump(params, f)
        return outputFileName

    def createValhallaJsonConfig(self, content):
        outputFileName = os.path.join(self._appDataDir(), 'valhalla.json')
        templatePath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "valhalla")
        templateFileLoader = FileSystemLoader(templatePath)
        jinjaEnv = Environment(loader=templateFileLoader)
        valhallaConfigTemplate = jinjaEnv.get_template('valhalla.json.jinja')
        with open(outputFileName, 'w') as f:
            f.write(valhallaConfigTemplate.render(valhallaTilesDir=content['valhallaTilesDir']))
        return outputFileName

    def _valhallaExecutablePath(self):
        kadasFolder = os.path.join(os.environ['PROGRAMFILES'], 'KadasAlbireo')
        defaultValhallaExeDir = os.path.join(kadasFolder, 'opt', 'routing')
        valhallaPath = QgsSettings().value("/kadas/valhalla_exe_dir", defaultValhallaExeDir)
        return os.path.join(valhallaPath, "valhalla_service.exe")

    def _execute(self, action, request):
        defaultValhallaExeDir = r'C:/Program Files/KadasAlbireo/opt/routing'
        valhallaPath = QgsSettings().value("/kadas/valhalla_exe_dir", defaultValhallaExeDir)
        valhallaExecutable = os.path.join(valhallaPath, "valhalla_service.exe")
        defaultValhallaTilesDir = r'C:/Program Files/KadasAlbireo/share/kadas/routing/default/valhalla_tiles'
        os.chdir(valhallaPath)
        valhallaConfig = self.createValhallaJsonConfig({'valhallaTilesDir': QgsSettings().value(
            "/kadas/valhalla_tiles_dir",
            defaultValhallaTilesDir)})
        commands = [valhallaExecutable, valhallaConfig, action, request]
        result = subprocess.run(commands, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True)
        response = json.loads(result.stdout.decode("utf-8"))
        if "error" in response:
            raise Exception(response["error"])
        return response

    def route(self, points, profile, options):
        params = self.prepareRouteParameters(points, profile, options)
        response = self._execute("route", json.dumps(params))
        return response

    def isochrones(self, points, profile, options, intervals, colors):
        params = self.prepareIsochronesParameters(points, profile, options,
                                                  intervals, colors)
        response = self._execute("isochrone", json.dumps(params))
        return response

    def mapmatching(self, shape, profile, options):
        params = self.prepareMapmatchingParameters(shape, profile, options)
        filename = self.createMapmatchingParametersFile(params)
        response = self._execute("trace_route", filename)
        return response


class HttpConnector(Connector):
    def __init__(self, url):
        self.url = url

    def _request(self, endpoint, payload):
        url = f"{self.url}/{endpoint}?json={payload}"
        logging.info("Requesting %s" % url)
        response = requests.get(url)
        # Custom handling for Valhalla to raise the detailed message also.
        if response.status_code == 400:
            raise Valhalla400Exception(response.text)
        response.raise_for_status()
        return response.json()

    def route(self, points, profile, options):
        params = self.prepareRouteParameters(points, profile, options)
        response = self._request("route", json.dumps(params))
        return response

    def isochrones(self, points, profile, options, intervals, colors):
        params = self.prepareIsochronesParameters(points, profile, options,
                                                  intervals, colors)
        response = self._request("isochrone", json.dumps(params))
        return response

    def mapmatching(self, shape, profile, options):
        params = self.prepareMapmatchingParameters(shape, profile, options)
        url = f"{self.url}/trace_route"
        response = requests.post(url, json=params)
        if response.status_code == 400:
            raise Valhalla400Exception(response.text)
        response.raise_for_status()
        return response.json()
