"""League coverage is explicit: fields outside a league's scope are not missing."""
LEAGUES = {
    'spain': {'name':'LaLiga','country':'España','flag':'🇪🇸','cards':True,'corners':True,'split_season':True,'history_file':'LaLiga_RESUMEN_DINAMICO.xlsx'},
    'colombia': {'name':'Liga BetPlay','country':'Colombia','flag':'🇨🇴','cards':False,'corners':True,'split_season':False,'history_file':'Liga_Colombiana_RESUMEN_DINAMICO.xlsx'},
    'hungary': {'name':'NB I','country':'Hungría','flag':'🇭🇺','cards':False,'corners':False,'split_season':True,'history_file':'Hungria_NB_I_2018_2027.xlsx'},
}
