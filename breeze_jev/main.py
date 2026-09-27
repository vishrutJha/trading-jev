from .config import Settings

def main():
    import uvicorn
    s=Settings.from_env()
    uvicorn.run('breeze_jev.api:app',host=s.host,port=s.port,reload=False)

if __name__=='__main__': main()
