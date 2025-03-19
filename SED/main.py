import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import*
from db import*
from utility import*