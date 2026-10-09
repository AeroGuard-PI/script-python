import psutil as p
from datetime import datetime
import time
import socket
import os
import csv

hostname_atual = socket.gethostname()

captura_csv = "captura_python.csv"

if not os.path.exists(captura_csv):
    with open(captura_csv, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow([
            "Horario", 
            "Hostname", 
            "CPU_Porcentagem", 
            "CPU_Frequencia", 
            "RAM_Porcentagem", 
            "Memoria_Virtual_GB", 
            "Memoria_Total_GB", 
            "Memoria_Disponivel_GB", 
            "Disco_Porcentagem", 
            "Espaco_Disco_Total_GB"
        ])

while True:
    agora = datetime.now()
    horarioAtual = agora.strftime("%Y-%m-%d %H:%M:%S")    
    
    cpuPorcentagem = p.cpu_percent(interval=1)

    freq = p.cpu_freq()
    cpuFrequencia = freq.current if freq else 0.0
    
    ramPorcentagem = p.virtual_memory().percent
    
    memoriaVirtual = round(p.virtual_memory().used / (1024**3), 2)
    
    memoriaTotal = round(p.virtual_memory().total / (1024**3), 2)
    
    memoriaDisponivel = round(p.virtual_memory().available / (1024**3), 2)
    
    disco = p.disk_usage(os.path.abspath(os.sep))
    discoPorcentagem = disco.percent
    espacoDisco = round(disco.total / (1024**3), 2)
    
    with open(captura_csv, "a", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow([
            horarioAtual,
            hostname_atual,
            cpuPorcentagem,
            cpuFrequencia,
            ramPorcentagem,
            memoriaVirtual,
            memoriaTotal,
            memoriaDisponivel,
            discoPorcentagem,
            espacoDisco
        ])

    print(f"[{horarioAtual}] Métricas capturadas e salvas em {captura_csv}.")
    
    time.sleep(3)