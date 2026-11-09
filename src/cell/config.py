import yaml
import os

def load_config(config_path=None):
    if config_path is None:
        # default path relative to this file
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
        config_path = os.path.join(root, 'config', 'settings.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)