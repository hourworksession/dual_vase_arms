import yaml
import os

def load_config(config_path=None):
    if config_path is None:
        # default path relative to this file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)