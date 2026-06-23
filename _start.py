import os,sys  
os.chdir('E:\\frankenstein')  
sys.path.insert(0,'E:\\frankenstein')  
os.environ['PYTHONPATH']='E:\\frankenstein'  
from frankenstein.vision.api.main import app  
import uvicorn  
uvicorn.run(app,host='0.0.0.0',port=8000,log_level='info')  
