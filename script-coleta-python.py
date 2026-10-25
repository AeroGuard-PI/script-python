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
from tabulate import tabulate

load_dotenv()

def conexao_bd():
    conexao = mysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "AeroGuard")
    )

    cursor = conexao.cursor()
    print("""
+--------------------------------------------+-----------------------------------------+
|                    CONEXÃO COM O BANCO DE DADOS REALIZADA                            |
+--------------------------------------------+-----------------------------------------+
""")
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
        time.sleep(5)
        limpar_menu()
        msg_busca = f"Máquina encontrada com o ID {maquina_id}"
        print("""
+--------------------------------------------+-----------------------------------------+
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(msg_busca))
        time.sleep(1.5)
        sql_atualizar_maquina = """
            UPDATE maquina SET hostname = %s, so = %s, macAddress = %s
                WHERE id = %s AND fkEmpresa = %s
    """
        cursor.execute(sql_atualizar_maquina, (hostname, nome_so, mac_address, maquina_id, empresa_id))
        time.sleep(1.5)
        print("""
+--------------------------------------------+-----------------------------------------+
|                     SERVIDOR ATUALIZADO COM SUCESSO                                  |
+--------------------------------------------+-----------------------------------------+
""")
        time.sleep(1.5)
    else:
        sql_inserir_maquina = """
            INSERT INTO maquina (hostname, so, macAddress, fkEmpresa) VALUES (%s, %s, %s, %s)
        """
        cursor.execute(sql_inserir_maquina, (hostname, nome_so, mac_address, empresa_id))
        conexao.commit()
        maquina_id = cursor.lastrowid
        time.sleep(2)
        msg_cadastro = f"Máquina {maquina_id} cadastrada!"
        print("""
+--------------------------------------------+-----------------------------------------+
| {:<85} |
+--------------------------------------------+-----------------------------------------+
""".format(msg_cadastro))

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
        print("""
+--------------------------------------------+-----------------------------------------+
|           NENHUMA CONFIGURAÇÃO ENCONTRADA - APLICANDO PADRÃO                         |
+--------------------------------------------+-----------------------------------------+
""")
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

    print("\n")
    print("""
+--------------------------------------------+-----------------------------------------+
|                           COMPONENTES MONITORADOS                                    |
+--------------------------------------------+-----------------------------------------+""")
    for comp_id, comp_nome in componentes_ativos:
        item = f"ID {comp_id} - Componente {comp_nome}"
        print(f"| {item:<84} |")
        time.sleep(1)
    print("+--------------------------------------------+-----------------------------------------+")
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


            if id_discretizacao == 2 or id_discretizacao == 3:
                sql_inserir_alerta = """
                    INSERT INTO historico_alerta (data_hora, maquina_captura_id, componentes_captura_id, discretizacao_id)
                    VALUES (%s, %s, %s, %s)
                """
                cursor.execute(sql_inserir_alerta, (
                    horario,
                    captura_id,
                    captura_id, 
                    id_discretizacao,
                ))

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

    print("""
+--------------------------------------------+-----------------------------------------+
|                                  MENU PRINCIPAL                                      |
+--------------------------------------------+-----------------------------------------+
| Momento atual da consulta: {:<57} |
+--------------------------------------------+-----------------------------------------+
| 1. Informações sobre o servidor                                                      |
| 2. Consulta completa de recursos                                                     |
| 3. Consultar dados armazenados                                                       |""".format(horario_atual))
    
    opcao = 4
    for comp_id, comp_nome in componentes_ativos:
        item = f"{opcao}. Opções de {comp_nome}"
        print(f"| {item:<84} |")
        opcao += 1

    print("""| 0. Sair                                                                              |
+--------------------------------------------+-----------------------------------------+
""")
      

def informacoes_servidor():
    limpar_menu()
    hostname = socket.gethostname()
    so = platform.system()
    versao = platform.release()
    mac = get_mac_address()
    n_fisicos = p.cpu_count(logical=False)
    n_logicos = p.cpu_count(logical=True)

    print("""
+--------------------------------------------+-----------------------------------------+
|                             INFORMAÇÕES DO SERVIDOR                                  |
+--------------------------------------------+-----------------------------------------+
| Nome do servidor: {:<66} |
| Sistema operacional: {:<63} |
| Versão do sistema: {:<65} |
| MacAddress: {:<72} |
| Núcleos físicos: {:<67} |
| Núcleos lógicos: {:<67} |
+--------------------------------------------+-----------------------------------------+
""".format(hostname, so, versao, str(mac), str(n_fisicos), str(n_logicos)))

    voltar()


def consulta_completa(cursor, conexao, maquina_id, componentes_ativos):
    limpar_menu()

    capturas_alertas(cursor, conexao, maquina_id, componentes_ativos)
    horarioAtual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    print("""
+--------------------------------------------+-----------------------------------------+
|                                CAPTURA COMPLETA                                      |
+--------------------------------------------+-----------------------------------------+
| Horário da captura: {:<64} |
+--------------------------------------------+-----------------------------------------+""".format(horarioAtual))

    for comp_id, comp_nome in componentes_ativos:
        nome_formatado = comp_nome.strip().lower().replace("é", "e").replace("ó","o")

        if nome_formatado == "cpu":
            dados_cpu = coletar_cpu()
            freq = f"{dados_cpu['frequencia_cpu']:.2f} MHz" if dados_cpu["frequencia_cpu"] else "indisponivel"
            u_cpu = f"Uso de CPU: {dados_cpu['uso_cpu']:.2f}%"
            f_cpu = f"Frequência: {freq}"
            print(f"| {u_cpu:<84} |")
            print(f"| {f_cpu:<84} |")
        elif nome_formatado == "memoria":
            dados_memoria = coletar_memoria()
            u_mem = f"Uso de memória: {dados_memoria['uso_memoria']:.2f}%"
            t_mem = f"Memória total: {dados_memoria['memoria_total']:.2f} GB"
            d_mem = f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GB"
            print(f"| {u_mem:<84} |")
            print(f"| {t_mem:<84} |")
            print(f"| {d_mem:<84} |")
        elif nome_formatado == "disco":
            dados_disco = coletar_disco()
            u_disc = f"Uso do Disco: {dados_disco['uso_disco']:.2f}%"
            print(f"| {u_disc:<84} |")

    print("+--------------------------------------------+-----------------------------------------+")
    voltar()


def consulta_banco(cursor):
    limpar_menu()
    empresa_id = 1  
    sql = """
        SELECT c.tipo, c.valor, c.unidade_medida, c.componentes_id, c.maquina_id, m.fkEmpresa
            FROM captura AS c
                JOIN maquina AS m ON c.maquina_id = m.id
                    WHERE m.fkEmpresa = %s
                        ORDER BY c.data_hora DESC LIMIT 10
    """
    
    cursor.execute(sql, (empresa_id,))
    resultado_bd = cursor.fetchall()

    print("+----------------------------------------------------------------------------------------------+")
    print("|                                 INFORMAÇÕES DO BANCO DE DADOS                                |")
    print("+----------------------------------------------------------------------------------------------+")

    print(tabulate(
            resultado_bd,
            headers=["Tipo", "Valor", "Unidade de medida", "ID componente", "ID Servidor", "ID empresa"],
            tablefmt="grid"
        ))

    voltar()


def menu_cpu(maquina_id):
    while True:
        limpar_menu()

        print("""
+--------------------------------------------+-----------------------------------------+
|                                  MENU DE CPU                                         |
+--------------------------------------------+-----------------------------------------+
| 1. Consultar uso de CPU                                                              |
| 2. Consultar núcleos lógicos e físicos                                               |
| 3. Consultar frequência de CPU                                                       |
| 0. Para retornar                                                                     |
+--------------------------------------------+-----------------------------------------+
""")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_cpu = coletar_cpu()
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")
            uso_cpu = dados_cpu["uso_cpu"]
            status = discretizar(uso_cpu, "cpu", limite_alerta, limite_critico)
            u_cpu = f"Uso de CPU: {dados_cpu['uso_cpu']:.2f}%"
            s_cpu = f"STATUS: {status}"

            print("""
+--------------------------------------------+-----------------------------------------+
|                                  USO DA CPU                                          |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, u_cpu, s_cpu))
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_cpu = coletar_cpu()
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")
            n_fis = f"Núcleos físicos: {p.cpu_count(logical=False)}"
            n_log = f"Núcleos lógicos: {p.cpu_count(logical=True)}"

            print("""
+--------------------------------------------+-----------------------------------------+
|                                NÚCLEOS DA CPU                                        |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, n_fis, n_log))
            voltar()
        elif resposta == "3":
            limpar_menu()
            dados_cpu = coletar_cpu()
            horarioAtual = dados_cpu["horario"].strftime("%d/%m/%Y %H:%M:%S")
            freq_txt = f"{dados_cpu['frequencia_cpu']:.2f} MHz" if dados_cpu["frequencia_cpu"] is not None else "Frequência da CPU indisponível."
            f_txt = f"Frequência: {freq_txt}"

            print("""
+--------------------------------------------+-----------------------------------------+
|                              FREQUÊNCIA DA CPU                                       |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, f_txt))
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

        print("""
+--------------------------------------------+-----------------------------------------+
|                                MENU DE MEMÓRIA                                       |
+--------------------------------------------+-----------------------------------------+
| 1. Consultar uso de memória                                                          |
| 2. Consultar memória total e disponível                                              |
| 0. Para retornar                                                                     |
+--------------------------------------------+-----------------------------------------+
""")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_memoria = coletar_memoria()
            horarioAtual = dados_memoria["horario"].strftime("%d/%m/%Y %H:%M:%S")
            uso_memoria = dados_memoria["uso_memoria"]
            status = discretizar(uso_memoria, "memoria", limite_alerta, limite_critico)
            u_mem = f"Uso de memória: {dados_memoria['uso_memoria']:.2f}%"
            s_mem = f"STATUS: {status}"

            print("""
+--------------------------------------------+-----------------------------------------+
|                                USO DA MEMÓRIA                                        |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, u_mem, s_mem))
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_memoria = coletar_memoria()
            horarioAtual = dados_memoria["horario"].strftime("%d/%m/%Y %H:%M:%S")
            t_mem = f"Memória total: {dados_memoria['memoria_total']:.2f} GB"
            d_mem = f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GB"

            print("""
+--------------------------------------------+-----------------------------------------+
|                            CAPACIDADE DA MEMÓRIA                                     |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, t_mem, d_mem))
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

        print("""
+--------------------------------------------+-----------------------------------------+
|                                MENU DE DISCO                                         |
+--------------------------------------------+-----------------------------------------+
| 1. Consultar uso do disco principal                                                  |
| 2. Consultar espaço total e livre do disco                                           |
| 0. Para retornar                                                                     |
+--------------------------------------------+-----------------------------------------+
""")

        resposta = input("Digite a opção desejada: ").strip().lower()
        if resposta == "1":
            limpar_menu()
            dados_disco = coletar_disco()
            horarioAtual = dados_disco["horario"].strftime("%d/%m/%Y %H:%M:%S")
            uso_disco = dados_disco["uso_disco"]
            status = discretizar(uso_disco, "disco", limite_alerta, limite_critico)
            u_disc = f"Uso do disco: {dados_disco['uso_disco']:.2f}%"
            s_disc = f"STATUS: {status}"

            print("""
+--------------------------------------------+-----------------------------------------+
|                                USO DO DISCO                                          |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, u_disc, s_disc))
            voltar()
        elif resposta == "2":
            limpar_menu()
            dados_disco = coletar_disco()
            horarioAtual = dados_disco["horario"].strftime("%d/%m/%Y %H:%M:%S")

            caminho_disco = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
            disco = p.disk_usage(caminho_disco)
            disco_total = disco.total / (1024 ** 3)
            disco_livre = disco.free / (1024 ** 3)
            t_disc = f"Espaço total: {disco_total:.2f} GB"
            l_disc = f"Espaço livre: {disco_livre:.2f} GB"

            print("""
+--------------------------------------------+-----------------------------------------+
|                               ESPAÇO DO DISCO                                        |
+--------------------------------------------+-----------------------------------------+
| Horário atual: {:<69} |
| {:<84} |
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(horarioAtual, t_disc, l_disc))
            voltar()
        elif resposta == "0":
            limpar_menu()
            break
        else:
            print("Opção inválida, tente novamente")
            time.sleep(2)

def main():
    limpar_menu()
    print("""
+--------------------------------------------+-----------------------------------------+
|                    INICIANDO O SISTEMA DE MONITORAMENTO                              |
+--------------------------------------------+-----------------------------------------+
""")
    time.sleep(2)
    limpar_menu()

    print("""
+--------------------------------------------+-----------------------------------------+
|                    CONECTANDO AO BANCO DE DADOS...                                   |
+--------------------------------------------+-----------------------------------------+
""")
    time.sleep(2.5)

    conexao, cursor = conexao_bd()
    maquina_id = identificacao_maquina(conexao, cursor)
    componentes_ativos = obter_componentes(conexao, cursor, maquina_id)

    info_servidor = f"ID do servidor: {maquina_id}"
    print("""
+--------------------------------------------+-----------------------------------------+
| {:<84} |
+--------------------------------------------+-----------------------------------------+
""".format(info_servidor))

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
            time.sleep(2.5)
            print("Programa encerrado.")
            break
        else:
            print("Opção inválida, tente novamente.")
            time.sleep(2.5)
            limpar_menu()

    cursor.close()
    conexao.close()

main()