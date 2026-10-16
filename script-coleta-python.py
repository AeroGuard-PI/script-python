import os
import socket
import platform
import mysql.connector as mysql
from dotenv import load_dotenv
from getmac import get_mac_address
import psutil as p
from datetime import datetime
import time

load_dotenv()

conexao = mysql.connect(
    host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "aeroguard")
)

cursor = conexao.cursor()
print("Conexão com o banco de dados realizada com sucesso!")

hostname = socket.gethostname()
nome_so = platform.system()
mac_address = get_mac_address()
empresa_id = 1

sql_buscar_maquina = "SELECT id FROM maquina WHERE hostname = %s"
cursor.execute(sql_buscar_maquina, (hostname,))
resultado_maquina = cursor.fetchone()

if resultado_maquina is not None:
    maquina_id = resultado_maquina[0]
    print(f"Máquina encontrada com o ID {maquina_id}")
    sql_atualizar_maquina = """
        UPDATE maquina SET hostname = %s, so = %s, macAddress = %s
            WHERE id = %s AND fkEmpresa = %s
"""
    cursor.execute(sql_atualizar_maquina, (hostname, nome_so, mac_address, maquina_id, empresa_id))
    print("Servidor atualizado com sucesso")
else:
    sql_inserir_maquina = """
        INSERT INTO maquina (hostname, so, macAddress, fkEmpresa) VALUES (%s, %s, %s, %s)
    """
    cursor.execute(sql_inserir_maquina, (hostname, nome_so, mac_address, empresa_id))
    conexao.commit()
    maquina_id = cursor.lastrowid
    print(f"Máquina {maquina_id} cadastrada!")

sql_buscar_comp = """
    SELECT c.id, c.nome FROM configuracao_maquina AS cm
        JOIN componentes AS c
            WHERE cm.maquina_id = %s AND disponibilidade = 'ATIVO'
"""
cursor.execute(sql_buscar_comp, (maquina_id,))
componentes_ativos = cursor.fetchall()

if componentes_ativos is None:
    print("Nenhuma configuração encontrada. Aplicando configuração padrão")
    cursor.execute("SELECT id, nome FROM componentes")
    todos_componentes = cursor.fetchall()

    sql_inserir_config = """
    INSERT INTO configuracao_maquina (maquina_id, componentes_id, disponibilidade) VALUES (%s, %s, 'ATIVO')
"""
    for comp_id, comp_nome in todos_componentes:
        cursor.execute(sql_inserir_config, (maquina_id, comp_id))

    conexao.commit()
    componentes_ativos = todos_componentes
    print("Configuração padrão criada")

print("Componentes monitorados:")
for comp_id, comp_nome in componentes_ativos:
    print(f"ID {comp_id} - Componente {comp_nome}")


limite_alerta = 80
limite_critico = 90
controle_tempo = {}

def discretizar(valor, componente, limite_alerta, limite_critico):
    horario_atual = time.time()

    if componente not in controle_tempo:
        controle_tempo[componente] = {
            "inicio_critico": None,
            "tempo_seg": 0
        }

    estado = controle_tempo[componente]

    if valor < limite_alerta:
        estado["inicio_critico"] = None
        estado["tempo_seg"] = 0
        return "NORMAL"

    elif valor < limite_critico:
        estado["inicio_critico"] = None
        estado["tempo_seg"] = 0
        return "ALERTA"

    else:
        if estado["inicio_critico"] is None:
            estado["inicio_critico"] = horario_atual

        tempo_decorrido = horario_atual - estado["inicio_critico"]
        estado["tempo_seg"] = tempo_decorrido

        if tempo_decorrido >= 30:
            return "CRÍTICO"
        else:
            return "ALERTA (PICO EM OBSERVAÇÃO)"
        

def coletar_cpu():
    agora = datetime.now()
    uso_cpu = p.cpu_percent(interval=0.5)
    frequencia = p.cpu_freq(percpu=False)
    frequencia_cpu = frequencia.current if frequencia else None
    # status_cpu = discretizar_cpu(uso_cpu)

    return{
        "horario": agora,
        "uso_cpu": uso_cpu,
        "frequencia": frequencia,
        "frequencia_cpu": frequencia_cpu
    }

def coletar_memoria():
    agora = datetime.now()
    memoria = p.virtual_memory()
    memoria_total = memoria.total / (1024 ** 3)
    memoria_disponivel = memoria.available / (1024 ** 3)
    uso_memoria = memoria.percent
    # status_memoria = discretizar_memoria(uso_memoria)

    return {
        "horario": agora,
        "memoria": memoria,
        "memoria_total": memoria_total,
        "memoria_disponivel": memoria_disponivel,
        "uso_memoria": uso_memoria
    }

def coletar_disco():
    agora = datetime.now()
    caminho_disco = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    disco = p.disk_usage(caminho_disco)
    # status_disco = discretizar_disco(disco.percent)

    return {
        "horario": agora,
        "uso_disco": disco.percent
    }

funcoes_coleta = {
    "cpu": coletar_cpu,
    "memoria": coletar_memoria,
    "disco": coletar_disco
}

for comp_id, comp_nome in componentes_ativos:
    nome_formatado = comp_nome.strip().lower()

    if nome_formatado in funcoes_coleta:
        funcao = funcoes_coleta[nome_formatado]
        dados = funcao()

        valor = None
        if nome_formatado == "cpu":
            valor = dados["uso_cpu"]
        elif nome_formatado == "memoria":
            valor = dados["uso_memoria"]
        elif nome_formatado == "disco":
            valor = dados["uso_disco"]

        horario = dados["horario"]
        status = discretizar(valor, nome_formatado, LIMITE_ALERTA, LIMITE_CRITICO)

        # deu certo?
        print(f"Coletando {comp_nome}: {valor}% | Status: {status}")

        sql_inserir_captura = """
            INSERT INTO captura (maquina_id, componentes_id, tipo, valor, data_hora, unidade_medida) VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql_inserir_captura, (
            maquina_id,
            comp_id,
            f"Uso {comp_nome}",
            valor,
            horario,
            "%"
        ))

        captura_id = cursor.lastrowid

        if status != "NORMAL":
            sql_inserir_alerta = """
                INSERT INTO historico_alerta (maquina_captura_id, componentes_captura_id, discretizacao_id, data_hora)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql_inserir_alerta, (
                captura_id,
                comp_id,
                None,
                horario
            ))
            print(f"Alerta: ({status}) gravado da captura: {captura_id}")

        conexao.commit()

cursor.close()
conexao.close()
print("Processo finalizado.")