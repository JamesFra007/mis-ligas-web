"""Run an incremental source update without opening the browser."""
import argparse
import json
from fastapi import HTTPException
from . import db
from .main import perform_sync


def main():
    parser=argparse.ArgumentParser(description='Actualizar partidos de Mis Ligas')
    parser.add_argument('league',choices=['spain','colombia'])
    parser.add_argument('season',help='2026-27 para España; 2026 para Colombia')
    args=parser.parse_args();db.initialize()
    try:
        print(json.dumps(perform_sync(args.league,args.season),ensure_ascii=False,indent=2))
    except HTTPException as error:
        parser.exit(1,str(error.detail)+'\n')


if __name__=='__main__':main()
