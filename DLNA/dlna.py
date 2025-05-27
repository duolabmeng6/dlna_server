import os
import re
import sys
import logging
import importlib

from .utils import SETTING_DIR
from .protocol import DLNAProtocol
from .server import Service
from .utils import RENDERER_DIR, PROTOCOL_DIR, Setting

logger = logging.getLogger("main")
logger.setLevel(logging.DEBUG)


class DLNAPlugin:
    def __init__(self, path, title="None", plugin_instance=None, platform='none'):
        # path is allowed to be set to None only when renderer is DLNAdefault plugin
        self.path = path
        self.title = title
        self.plugin_class = None
        self.plugin_instance = plugin_instance
        self.platform = platform
        if path:
            try:
                self.load_from_file(path)
            except Exception as e:
                logger.error(str(e))

    def get_info(self):
        props = ['protocol', 'title', 'renderer', 'platform', 'version', 'author', 'desc']
        res = {'default': False}
        for i in props:
            res[i] = getattr(self, i, '')
        if getattr(self, 'renderer', None) is not None:
            res['type'] = 'renderer'
        if getattr(self, 'protocol', None) is not None:
            res['type'] = 'protocol'
        if self.path is None:
            res['default'] = True
            res['desc'] = 'DLNA default plugin'
            res['version'] = Setting.version
        return res

    def get_instance(self):
        if self.plugin_instance is None and self.plugin_class is not None:
            self.plugin_instance = self.plugin_class()
        return self.plugin_instance

    def check(self):
        """ Check if this renderer can run on your device
        """
        if self.plugin_class is None:
            return False
        if sys.platform in self.platform:
            return True
        logger.error("{} support platform: {}".format(self.title, self.platform))
        logger.error("{} is not suit for this system.".format(self.title))
        return False

    def load_from_file(self, path):
        base_name = os.path.basename(path)[:-3]
        with open(path, 'r', encoding='utf-8') as f:
            renderer_file = f.read()
            metadata = re.findall("<DLNA.(.*?)>(.*?)</DLNA", renderer_file)
            print("<Load Plugin from {}".format(base_name))
            for key, value in metadata:
                print('%-10s: %s' % (key, value))
                setattr(self, key, str(value))
        if hasattr(self, 'renderer'):
            module = importlib.import_module(f'{RENDERER_DIR}.{base_name}')
            print(f'Load plugin {self.renderer} done />\n')
            self.plugin_class = getattr(module, self.renderer, None)
        elif hasattr(self, 'protocol'):
            module = importlib.import_module(f'{PROTOCOL_DIR}.{base_name}')
            print(f'Load plugin {self.protocol} done />\n')
            self.plugin_class = getattr(module, self.protocol, None)
        else:
            logger.error(f"Cannot find any plugin in {base_name}")
            return


class DLNAPluginManager:
    def __init__(self, renderer_default, protocol_default):
        sys.path.append(SETTING_DIR)
        self.create_plugin_dir(RENDERER_DIR)
        self.create_plugin_dir(PROTOCOL_DIR)
        self.renderer_list = [renderer_default]
        self.renderer_list += self.load_DLNA_plugin(RENDERER_DIR)
        self.protocol_list = [protocol_default]
        self.protocol_list += self.load_DLNA_plugin(PROTOCOL_DIR)

    def get_renderer(self, name):
        plugin = self.get_plugin_from_list(self.renderer_list, name)
        return plugin.get_instance()

    def get_protocol(self, name):
        plugin = self.get_plugin_from_list(self.protocol_list, name)
        return plugin.get_instance()

    def get_info(self):
        res = []
        for r in self.renderer_list:
            res.append(r.get_info())
        for p in self.protocol_list:
            res.append(p.get_info())
        return res

    @staticmethod
    def get_plugin_from_list(plugin_list, title) -> DLNAPlugin:
        for i in plugin_list:
            if title == i.title:
                print("using plugin: {}".format(title))
                return i
        else:
            print("using default plugin")
            return plugin_list[0]

    @staticmethod
    def load_DLNA_plugin(path: str):
        plugin_path = os.path.join(SETTING_DIR, path)
        if not os.path.exists(plugin_path):
            return []
        plugin_list = []
        plugins = os.listdir(plugin_path)
        plugins = filter(lambda s: s.endswith('.py') and s != '__init__.py', plugins)
        for plugin in plugins:
            path = os.path.join(plugin_path, plugin)
            plugin_config = DLNAPlugin(path)
            if plugin_config.check():
                plugin_list.append(plugin_config)
        return plugin_list

    @staticmethod
    def create_plugin_dir(path):
        custom_module_path = os.path.join(SETTING_DIR, path)
        if not os.path.exists(custom_module_path):
            os.makedirs(custom_module_path)
        init_file_path = os.path.join(custom_module_path, '__init__.py')
        if not os.path.exists(init_file_path):
            open(init_file_path, 'a').close()


def cli(renderer=None, protocol=None):
    if renderer is None:
        renderer = DLNAProtocol()
    if protocol is None:
        protocol = DLNAProtocol()
    service = Service(renderer=renderer, protocol=protocol)
    try:
        service.run()
    except KeyboardInterrupt:
        service.stop()
