import os
import socket
import platform
import time
import psutil as p
from datetime import datetime
import mysql.connector as mysql
from dotenv import load_dotenv
from getmac import get_mac_address
import threading

load_dotenv()

def conexao_bd():
    conexao = mysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "AeroGuard")
    )

    cursor = conexao.cursor()
    print("Conexão com o banco de dados realizada com sucesso!")
    return conexao, cursor

def identificacao_maquina(conexao, cursor):
    hostname = socket.gethostname()
    nome_so = platform.system()
    mac_address = get_mac_address()
    empresa_id = 1

    sql_buscar_maquina = "SELECT id FROM maquina WHERE hostname = %s and fkEmpresa = %s"
    cursor.execute(sql_buscar_maquina, (hostname, empresa_id))
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

    return maquina_id


def obter_componentes(conexao, cursor, maquina_id):
    sql_buscar_comp = """
        SELECT c.id, c.nome FROM configuracao_maquina AS cm
            JOIN componentes AS c ON cm.componentes_id = c.id
                WHERE cm.maquina_captura_id = %s AND disponibilidade = 'ATIVO'
    """
    cursor.execute(sql_buscar_comp, (maquina_id,))
    componentes_ativos = cursor.fetchall()

    if not componentes_ativos:
        print("Nenhuma configuração encontrada. Aplicando configuração padrão")
        cursor.execute("SELECT id, nome FROM componentes WHERE nome IN ('CPU', 'Memória', 'Disco')")
        todos_componentes = cursor.fetchall()

        sql_inserir_config = """
        INSERT INTO configuracao_maquina (disponibilidade, maquina_captura_id, componentes_id) VALUES ('ATIVO', %s, %s)
    """
        for comp_id, comp_nome in todos_componentes:
            cursor.execute(sql_inserir_config, (maquina_id, comp_id))

        conexao.commit()
        componentes_ativos = todos_componentes
        print("Configuração padrão criada")

    print("Componentes monitorados:")
    for comp_id, comp_nome in componentes_ativos:
        print(f"ID {comp_id} - Componente {comp_nome}")
    return componentes_ativos


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

def capturas_alertas(cursor, conexao, maquina_id, componentes_ativos):
    for comp_id, comp_nome in componentes_ativos:
        nome_formatado = comp_nome.strip().lower().replace("ó", "o").replace("é", "e")

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
            status = discretizar(valor, nome_formatado, limite_alerta, limite_critico)

            # deu certo?
            # print(f"Coletando {comp_nome}: {valor}% | Status: {status}")

            sql_inserir_captura = """
                INSERT INTO captura (tipo, valor, data_hora, unidade_medida, maquina_id, componentes_id, configuracao_maquina_maquina_id, configuracao_maquina_componentes_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_inserir_captura, (
                f"Uso {comp_nome}",
                valor,
                horario,
                "%",
                maquina_id,
                comp_id,
                maquina_id,
                comp_id             
            ))

            captura_id = cursor.lastrowid

            id_discretizacao = 1
            if status != "NORMAL":
                if "CRÍTICO" in status:
                    id_discretizacao = 3
                else:
                    id_discretizacao = 2

            sql_inserir_alerta = """
                INSERT INTO historico_alerta (data_hora, maquina_captura_id, componentes_captura_id, discretizacao_id)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql_inserir_alerta, (
                horario,
                captura_id,
                comp_id,
                id_discretizacao,
            ))
                # print(f"Alerta: ({status}) gravado da captura: {captura_id}")

            conexao.commit()



def limpar_menu():
    os.system("cls" if os.name == "nt" else "clear")

def voltar():
    print()
    input("Digite ENTER para voltar ao menu...")
    limpar_menu()


def captura_continua(conexao, cursor, maquina_id, componentes_ativos):
    executando = True

    while executando:
        capturas_alertas(cursor, conexao, maquina_id, componentes_ativos)
        time.sleep(5)


def menu_principal(componentes_ativos):
    horario_atual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    print("--------------------------------------------")
    print("MENU PRINCIPAL")
    print("--------------------------------------------")
    print(f"Momento atual da consulta: {horario_atual}")
    print("--------------------------------------------")
    print("[1] Informações sobre o servidor")
    print("[2] Consulta completa de recursos")
    print("[3] Consultar dados armazenados")
    
    opcao = 4
    for comp_id, comp_nome in componentes_ativos:
        print(f"[{opcao}] Opções de {comp_nome}")
        opcao = opcao + 1
    print("[0] - Sair")



def informacoes_servidor():
    limpar_menu()

    print("--------------------------------------------")
    print("INFORMAÇÕES DO SERVIDOR")
    print("--------------------------------------------")
    print(f"Nome do servidor: {socket.gethostname()}")
    print(f"Sistema operaional: {platform.system()}")
    print(f"Versão do sistema: {platform.release()}")
    print(f"MacAddres: {get_mac_address()}")
    print(f"Núcleos físicos: {p.cpu_count(logical=False)}")
    print(f"Núcleos lógicos: {p.cpu_count(logical=True)}")  

    voltar()  


def consulta_completa(cursor, conexao, maquina_id, componentes_ativos):
    limpar_menu()

    capturas_alertas(cursor, conexao, maquina_id, componentes_ativos)
    horarioAtual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    print("--------------------------------------------")
    print("CAPTURA COMPLETA")
    print("--------------------------------------------")
    print("Realizando coleta dos componentes ativos...")
    print("--------------------------------------------")
    print(f"Horário da captura: {horarioAtual}")

    for comp_id, comp_nome in componentes_ativos:
        nome_formatado = comp_nome.strip().lower().replace("é", "e").replace("ó","o")

        if nome_formatado == "cpu":
            dados_cpu = coletar_cpu()
            freq = f"{dados_cpu['frequencia_cpu']:.2f} MHz" if dados_cpu["frequencia_cpu"] else "indisponivel"
            print()
            print(f"Uso de CPU: {dados_cpu['uso_cpu']:.2f}%")
            print(f"Frequência: {freq}")
        elif nome_formatado == "memoria":
            dados_memoria = coletar_memoria()

            print(f"Uso de memória: {dados_memoria['uso_memoria']:.2f}%")
            print(f"Memória total: {dados_memoria['memoria_total']:.2f} GB")
            print(f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GB")
        elif nome_formatado == "disco":
            dados_disco = coletar_disco()
            print(f"Uso do Disco: {dados_disco['uso_disco']:.2f}%")

    voltar()

def consulta_banco(cursor):
    limpar_menu()
    # ajustar esse select
    cursor.execute("SELECT * FROM captura ORDER BY data_hora DESC LIMIT 10")
    resultado_bd = cursor.fetchall()

    print("--------------------------------------------")
    print("INFORMAÇÕES DO BANCO DE DADOS")
    print("--------------------------------------------")
    # adicionar tabulate aqui, resultado provisório, ainda vou ajustar
    for resultado in resultado_bd:
        print(resultado)

    voltar()

def menu_cpu(maquina_id):
    while True:
        limpar_menu()

        print("MENU DE CPU")
        print("[1] Consultar uso de CPU")
        print("[2] Consultar núcleos lógicos e físicos")
        print("[3] Consultar frequência de CPU")
        print("[0] Para retornar")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_cpu = coletar_cpu()
            # capturas_alertas(maquina_id, dados_cpu)
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")

            print("--------------------------------------------")
            print("USO DA CPU")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Uso de CPU: {dados_cpu['uso_cpu']:.2f}%")
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_cpu = coletar_cpu()
            # capturas_alertas(maquina_id, dados_cpu)
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")
            print("--------------------------------------------")
            print("NÚCLEOS DA CPU")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Núcleos físicos: {p.cpu_count(logical=False)}")
            print(f"Núcleos lógicos: {p.cpu_count(logical=True)}")
            voltar()
        elif resposta == "3":
            limpar_menu()
            dados_cpu = coletar_cpu()
            # capturas_alertas(maquina_id, dados_cpu)
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")
            freq_txt = f"{dados_cpu['frequencia_cpu']:.2f} MHz" if dados_cpu["frequencia_cpu"] is not None else "Frequência da CPU indisponível."
            print("--------------------------------------------")
            print("FREQUÊNCIA DA CPU")
            print("--------------------------------------------") 
            print(f"Horário atual: {horarioAtual}")   
            print(f"Frequência: {freq_txt}")        
            voltar()
        elif resposta == "0":
            limpar_menu()
            break
        else:
            print("Opção inválida, tente novamente")
            time.sleep(2)
            

def menu_memoria(maquina_id):
    while True:
        limpar_menu()

        print("MENU DE MEMÓRIA")
        print("[1] Consultar uso de memória")
        print("[2] Consultar memória total e disponível")
        print("[0] Para retornar")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_memoria = coletar_memoria()
            horarioAtual = dados_memoria["horario"].strftime("%d/%m/%Y %H:%M:%S")

            print("--------------------------------------------")
            print("USO DA MEMÓRIA")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Uso de memória: {dados_memoria['uso_memoria']:.2f}%")
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_memoria = coletar_memoria()
            horarioAtual = dados_memoria["horario"].strftime("%d/%m/%Y %H:%M:%S")

            print("--------------------------------------------")
            print("CAPACIDADE DA MEMÓRIA")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Memória total: {dados_memoria['memoria_total']:.2f} GB")
            print(f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GB")
            voltar()
        elif resposta == "0":
            limpar_menu()
            break
        else:
            print("Opção inválida, tente novamente")
            time.sleep(2)


def menu_disco(maquina_id):
    while True:
        limpar_menu()

        print("MENU DE DISCO")
        print("[1] Consultar uso do disco principal")
        print("[2] Consultar espaço total e livre do disco")
        print("[0] Para retornar")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_disco = coletar_disco()
            horarioAtual = dados_disco["horario"].strftime("%d/%m/%Y %H:%M:%S")

            print("--------------------------------------------")
            print("USO DO DISCO")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Uso do disco: {dados_disco['uso_disco']:.2f}%")
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_disco = coletar_disco()
            horarioAtual = dados_disco["horario"].strftime("%d/%m/%Y %H:%M:%S")

            caminho_disco = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
            disco = p.disk_usage(caminho_disco)
            disco_total = disco.total / (1024 ** 3)
            disco_livre = disco.free / (1024 ** 3)

            print("--------------------------------------------")
            print("ESPAÇO DO DISCO")
            print("--------------------------------------------")
            print(f"Horário atual: {horarioAtual}")
            print(f"Espaço total: {disco_total:.2f} GB")
            print(f"Espaço livre: {disco_livre:.2f} GB")
            voltar()
        elif resposta == "0":
            limpar_menu()
            break
        else:
            print("Opção inválida, tente novamente")
            time.sleep(2)

def main():
    limpar_menu()
    print("Iniciando o sistema de monitoramento...")
    print("Conectando ao banco de dados...")

    conexao, cursor = conexao_bd()
    maquina_id = identificacao_maquina(conexao, cursor)
    componentes_ativos = obter_componentes(conexao, cursor, maquina_id)

    print(f"ID do servidor: {maquina_id}")

    thread_secundaria = threading.Thread(target=captura_continua, args=(conexao, cursor, maquina_id, componentes_ativos), daemon=True)
    thread_secundaria.start()

    time.sleep(2)
    limpar_menu()

    while True:
        menu_principal(componentes_ativos)
        resposta = input("Digite a opção desejada: ").strip().lower()

        if resposta == "1":
            informacoes_servidor()
        elif resposta == "2":
            consulta_completa(cursor, conexao, maquina_id, componentes_ativos)
        elif resposta == "3":
            consulta_banco(cursor)
        elif resposta == "4":
            menu_cpu(maquina_id)
        elif resposta == "5":
            menu_memoria(maquina_id)
        elif resposta == "6":
            menu_disco(maquina_id)
        elif resposta == "0":
            limpar_menu()
            print("Encerrando o sistema de monitoramento...")
            time.sleep(2)
            print("Programa encerrado.")
            break
        else:
            print("Opção inválida, tente novamente.")
            time.sleep(2)
            limpar_menu()

    cursor.close()
    conexao.close()

main()