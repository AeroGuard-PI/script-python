import os
import socket
import platform
import time
import psutil as p
from datetime import datetime, timezone
from pathlib import Path
import re
import signal
import sys
import mysql.connector as mysql
from dotenv import load_dotenv
import threading
from tabulate import tabulate

load_dotenv(os.getenv("AEROGUARD_ENV", Path(__file__).resolve().parent / ".env"), interpolate=False)

HOST_ROOT = Path(os.getenv("HOST_ROOT", "/"))
if HOST_ROOT != Path("/"):
    p.PROCFS_PATH = str(HOST_ROOT / "proc")

def variavel(nome):
    valor = os.getenv(nome, "")
    if not valor.strip():
        raise ValueError(f"Preencha {nome}.")
    return valor



def normalizar_mac(mac):
    texto = mac.strip().replace(":", "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", texto) or texto in ("000000000000", "ffffffffffff"):
        raise ValueError("MAC_ADDRESS inválido.")
    return ":".join(texto[i:i + 2] for i in range(0, 12, 2))



def mac_da_maquina():
    mac = normalizar_mac(variavel("MAC_ADDRESS"))
    for enderecos in p.net_if_addrs().values():
        for endereco in enderecos:
            if endereco.family == p.AF_LINK:
                try:
                    if normalizar_mac(endereco.address) == mac:
                        return mac
                except ValueError:
                    pass
    raise ValueError("O MAC configurado não foi encontrado nesta máquina.")



def cnpj_da_empresa():
    cnpj = variavel("EMPRESA_CNPJ").strip()
    if not re.fullmatch(r"[0-9]{14}", cnpj):
        raise ValueError("EMPRESA_CNPJ deve conter 14 dígitos, sem pontuação.")
    return cnpj



def sistema_operacional():
    arquivo = HOST_ROOT / "etc/os-release"
    if arquivo.is_file():
        for linha in arquivo.read_text().splitlines():
            if linha.startswith("PRETTY_NAME="):
                return linha.split("=", 1)[1].strip('"')[:50]
    return platform.system()[:50]


def conexao_bd():
    conexao = mysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=variavel("DB_USER"),
        password=variavel("DB_PASSWORD"),
        database="AeroGuard",
        autocommit=False,
        connection_timeout=3,
        read_timeout=5,
        write_timeout=5
    )
    cursor = conexao.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
        return conexao, cursor
    except mysql.Error:
        cursor.close()
        conexao.close()
        raise


def identificacao_maquina(conexao, cursor):
    mac = mac_da_maquina()
    hostname = socket.gethostname()[:50]
    sistema = sistema_operacional()
    cursor.execute("SELECT id FROM empresa WHERE cnpj = %s", (cnpj_da_empresa(),))
    empresa = cursor.fetchone()
    if empresa is None:
        raise ValueError("Empresa não cadastrada. Confira EMPRESA_CNPJ.")
    empresa_id = empresa[0]
    sql = """
        SELECT id FROM maquina
        WHERE fkEmpresa = %s
            AND LOWER(REPLACE(REPLACE(TRIM(macAddress), ':', ''), '-', '')) = %s
        FOR UPDATE
    """
    cursor.execute(sql, (empresa_id, mac.replace(":", "")))
    maquinas = cursor.fetchall()
    if not maquinas:
        cursor.execute("""
            INSERT INTO maquina (hostname, so, macAddress, fkEmpresa)
            VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE hostname = hostname
        """, (hostname, sistema, mac, empresa_id))
        cursor.execute(sql, (empresa_id, mac.replace(":", "")))
        maquinas = cursor.fetchall()
    if len(maquinas) != 1:
        raise ValueError("O MAC deve corresponder a um único cadastro de máquina.")
    maquina_id = maquinas[0][0]
    cursor.execute("UPDATE maquina SET hostname = %s, so = %s WHERE id = %s",
                   (hostname, sistema, maquina_id))
    return maquina_id



def obter_componentes(conexao, cursor, maquina_id):
    ativos = []
    for nome in ("CPU", "Memória", "Disco"):
        cursor.execute("SELECT id, nome FROM componentes WHERE nome = %s", (nome,))
        componentes = cursor.fetchall()
        if len(componentes) != 1:
            raise ValueError(f"Cadastre um único componente chamado {nome}.")
        componente_id = componentes[0][0]
        cursor.execute("""
            SELECT id, disponibilidade FROM configuracao_maquina
            WHERE maquina_captura_id = %s AND componentes_id = %s
            ORDER BY data_hora DESC, id DESC LIMIT 1 FOR UPDATE
        """, (maquina_id, componente_id))
        configuracao = cursor.fetchone()
        if configuracao is None:
            cursor.execute("""
                INSERT INTO configuracao_maquina (disponibilidade, maquina_captura_id, componentes_id)
                VALUES (1, %s, %s)
            """, (maquina_id, componente_id))
            configuracao = (cursor.lastrowid, 1)
        if configuracao[1] == 1:
            ativos.append((configuracao[0], componente_id, nome))
    return ativos



controle_tempo = {}

def discretizar(valor, componente, limite_alerta, limite_critico):
    limites = (limite_alerta, limite_critico)
    estado = controle_tempo.get(componente)
    if estado is None or estado["limites"] != limites:
        estado = {"inicio_critico": None, "limites": limites}
        controle_tempo[componente] = estado
    if valor < limite_critico:
        estado["inicio_critico"] = None
        return "NORMAL" if valor < limite_alerta else "ALERTA"
    agora = time.monotonic()
    if estado["inicio_critico"] is None:
        estado["inicio_critico"] = agora
    if agora - estado["inicio_critico"] >= 30:
        return "CRÍTICO"
    return "ALERTA (PICO EM OBSERVAÇÃO)"

        

def coletar_cpu():
    uso_cpu = p.cpu_percent(interval=0.5)
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        frequencia = p.cpu_freq(percpu=False)
    except (OSError, NotImplementedError, p.Error):
        frequencia = None
    frequencia_cpu = frequencia.current if frequencia else None

    return{
        "horario": agora,
        "uso_cpu": uso_cpu,
        "frequencia": frequencia,
        "frequencia_cpu": frequencia_cpu
    }

def coletar_memoria():
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    memoria = p.virtual_memory()
    memoria_total = memoria.total / (1024 ** 3)
    memoria_disponivel = memoria.available / (1024 ** 3)
    uso_memoria = memoria.percent

    return {
        "horario": agora,
        "memoria": memoria,
        "memoria_total": memoria_total,
        "memoria_disponivel": memoria_disponivel,
        "uso_memoria": uso_memoria
    }

def coletar_disco():
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    caminho_disco = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else str(HOST_ROOT)
    disco = p.disk_usage(caminho_disco)

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
    cursor.execute("""
        SELECT id, limite, nivel_alerta FROM discretizacao
        WHERE nivel_alerta IN ('ALERTA', 'CRÍTICO')
    """)
    niveis = {}
    for identificador, limite, nome in cursor.fetchall():
        if nome in niveis:
            raise ValueError(f"Existe mais de um nível {nome} em discretizacao.")
        niveis[nome] = (identificador, limite)
    if set(niveis) != {"ALERTA", "CRÍTICO"}:
        raise ValueError("Cadastre os níveis ALERTA e CRÍTICO.")
    if not 0 <= niveis["ALERTA"][1] < niveis["CRÍTICO"][1] <= 100:
        raise ValueError("Os limites devem estar entre 0 e 100, com ALERTA menor que CRÍTICO.")
    ativos = set()
    for configuracao_id, componente_id, nome in componentes_ativos:
        chave = nome.lower().replace("ó", "o")
        ativos.add(chave)
        dados = funcoes_coleta[chave]()
        campos = {"cpu": "uso_cpu", "memoria": "uso_memoria", "disco": "uso_disco"}
        valor = dados[campos[chave]]
        horario = dados["horario"]
        status = discretizar(valor, chave, niveis["ALERTA"][1], niveis["CRÍTICO"][1])
        cursor.execute("""
            INSERT INTO captura (tipo, valor, data_hora, unidade_medida, maquina_id,
                componentes_id, configuracao_maquina_maquina_id, configuracao_maquina_componentes_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (f"Uso {nome}", valor, horario, "%", maquina_id, componente_id,
              configuracao_id, configuracao_id))
        captura_id = cursor.lastrowid
        if status != "NORMAL":
            nivel = "CRÍTICO" if status == "CRÍTICO" else "ALERTA"
            cursor.execute("""
                INSERT INTO historico_alerta (data_hora, maquina_captura_id,
                    componentes_captura_id, discretizacao_id)
                VALUES (%s, %s, %s, %s)
            """, (horario, captura_id, captura_id, niveis[nivel][0]))
    for componente in list(controle_tempo):
        if componente not in ativos:
            del controle_tempo[componente]




def limpar_menu():
    os.system("cls" if os.name == "nt" else "clear")

def voltar():
    print()
    input("Digite ENTER para voltar ao menu...")
    limpar_menu()


def captura_continua(parar):
    while not parar.is_set():
        conexao = cursor = None
        try:
            conexao, cursor = conexao_bd()
            maquina_id = identificacao_maquina(conexao, cursor)
            componentes = obter_componentes(conexao, cursor, maquina_id)
            capturas_alertas(cursor, conexao, maquina_id, componentes)
            conexao.commit()
        except (mysql.Error, p.Error, OSError, ValueError) as erro:
            controle_tempo.clear()
            if conexao is not None:
                try:
                    conexao.rollback()
                except mysql.Error:
                    pass
            print(f"Falha na coleta: {erro}", file=sys.stderr, flush=True)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except mysql.Error:
                    pass
            if conexao is not None:
                try:
                    conexao.close()
                except mysql.Error:
                    pass
        parar.wait(5)



def menu_principal(componentes_menu):
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
    for comp_nome in componentes_menu:
        item = f"{opcao}. Opções de {comp_nome}"
        print(f"| {item:<84} |")
        opcao += 1

    print("""| 0. Sair                                                                              |
+--------------------------------------------+-----------------------------------------+
""")
      

def informacoes_servidor():
    limpar_menu()
    hostname = socket.gethostname()[:50]
    so = platform.system()
    versao = platform.release()
    mac = mac_da_maquina()
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


def consulta_completa(componentes_menu):
    limpar_menu()

    horarioAtual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    print("""
+--------------------------------------------+-----------------------------------------+
|                                CAPTURA COMPLETA                                      |
+--------------------------------------------+-----------------------------------------+
| Horário da captura: {:<64} |
+--------------------------------------------+-----------------------------------------+""".format(horarioAtual))

    for comp_nome in componentes_menu:
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
            t_mem = f"Memória total: {dados_memoria['memoria_total']:.2f} GiB"
            d_mem = f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GiB"
            print(f"| {u_mem:<84} |")
            print(f"| {t_mem:<84} |")
            print(f"| {d_mem:<84} |")
        elif nome_formatado == "disco":
            dados_disco = coletar_disco()
            u_disc = f"Uso do Disco: {dados_disco['uso_disco']:.2f}%"
            print(f"| {u_disc:<84} |")

    print("+--------------------------------------------+-----------------------------------------+")
    voltar()


def consulta_banco():
    limpar_menu()
    conexao = cursor = None
    try:
        conexao, cursor = conexao_bd()
        cursor.execute("""
            SELECT c.tipo, c.valor, c.unidade_medida, c.componentes_id, c.maquina_id, m.fkEmpresa
            FROM captura c
            JOIN maquina m ON c.maquina_id = m.id
            JOIN empresa e ON m.fkEmpresa = e.id
            WHERE LOWER(REPLACE(REPLACE(TRIM(m.macAddress), ':', ''), '-', '')) = %s
                AND e.cnpj = %s
            ORDER BY c.data_hora DESC, c.id DESC LIMIT 10
        """, (mac_da_maquina().replace(":", ""), cnpj_da_empresa()))
        print(tabulate(cursor.fetchall(),
                       headers=["Tipo", "Valor", "Unidade", "ID componente", "ID servidor", "ID empresa"],
                       tablefmt="grid"))
    finally:
        if cursor is not None:
            cursor.close()
        if conexao is not None:
            conexao.close()
    voltar()



def menu_cpu():
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
            u_cpu = f"Uso de CPU: {dados_cpu['uso_cpu']:.2f}%"
            s_cpu = "Leitura local; alertas são avaliados pela coleta em segundo plano."

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


def menu_memoria():
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
            u_mem = f"Uso de memória: {dados_memoria['uso_memoria']:.2f}%"
            s_mem = "Leitura local; alertas são avaliados pela coleta em segundo plano."

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
            t_mem = f"Memória total: {dados_memoria['memoria_total']:.2f} GiB"
            d_mem = f"Memória disponível: {dados_memoria['memoria_disponivel']:.2f} GiB"

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


def menu_disco():
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
            u_disc = f"Uso do disco: {dados_disco['uso_disco']:.2f}%"
            s_disc = "Leitura local; alertas são avaliados pela coleta em segundo plano."

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

            caminho_disco = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else str(HOST_ROOT)
            disco = p.disk_usage(caminho_disco)
            disco_total = disco.total / (1024 ** 3)
            disco_livre = disco.free / (1024 ** 3)
            t_disc = f"Espaço total: {disco_total:.2f} GiB"
            l_disc = f"Espaço livre: {disco_livre:.2f} GiB"

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
    if sys.argv[1:] not in ([], ["--coletar"]):
        print("Use --coletar para executar sem o menu.")
        return
    variavel("DB_USER")
    variavel("DB_PASSWORD")
    cnpj_da_empresa()
    mac_da_maquina()
    parar = threading.Event()
    def encerrar(*_):
        parar.set()
        if sys.argv[1:] != ["--coletar"]:
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, encerrar)
    signal.signal(signal.SIGINT, encerrar)
    if sys.argv[1:] == ["--coletar"]:
        captura_continua(parar)
        return
    thread = threading.Thread(target=captura_continua, args=(parar,))
    thread.start()
    componentes_menu = ["CPU", "Memória", "Disco"]
    menus = {"4": menu_cpu, "5": menu_memoria, "6": menu_disco}
    try:
        while not parar.is_set():
            menu_principal(componentes_menu)
            resposta = input("Digite a opção desejada: ").strip()
            try:
                if resposta == "0":
                    break
                elif resposta == "1":
                    informacoes_servidor()
                elif resposta == "2":
                    consulta_completa(componentes_menu)
                elif resposta == "3":
                    consulta_banco()
                elif resposta in menus:
                    menus[resposta]()
                else:
                    print("Opção inválida.")
            except (mysql.Error, p.Error, OSError, ValueError) as erro:
                print(f"Não foi possível concluir a consulta: {erro}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        parar.set()
        thread.join()
        print("Programa encerrado.")


if __name__ == "__main__":
    main()
