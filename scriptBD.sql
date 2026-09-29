CREATE DATABASE AeroGuard;
USE AeroGuard;

CREATE TABLE Localizacao (
    idLocalizacao INT PRIMARY KEY AUTO_INCREMENT,
    estado CHAR(2) NOT NULL,
    cidade VARCHAR(100) NOT NULL, 
    cep CHAR(8) NOT NULL,
    numero VARCHAR(10) NOT NULL
);

CREATE TABLE Aeroporto (
    idAeroporto INT PRIMARY KEY AUTO_INCREMENT,
    cnpj CHAR(14) NOT NULL UNIQUE, 
    nomeFantasia VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE, 
    telefone VARCHAR(15) NOT NULL,
    nome VARCHAR(100) NOT NULL,
    codigoIATA CHAR(3) NOT NULL UNIQUE, 
    fkLocalizacao INT NOT NULL, 
    codigo CHAR(5) NOT NULL UNIQUE,
    CONSTRAINT fk_Aeroporto_Localizacao 
        FOREIGN KEY (fkLocalizacao) REFERENCES Localizacao(idLocalizacao)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE User (
    idUser INT PRIMARY KEY AUTO_INCREMENT,
    nome VARCHAR(100) NOT NULL,
    cargo VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE, 
    senha VARCHAR(255) NOT NULL, 
    cpf CHAR(11) NOT NULL UNIQUE, 
    fkAeroportoUsuario INT NOT NULL, 
    CONSTRAINT fk_User_Aeroporto 
        FOREIGN KEY (fkAeroportoUsuario) REFERENCES Aeroporto(idAeroporto)
);

CREATE TABLE Maquina (
    idMaquina INT PRIMARY KEY AUTO_INCREMENT,
    hostname VARCHAR(50) NOT NULL UNIQUE, 
    fkAeroporto INT NOT NULL, 
    so VARCHAR(50) NOT NULL,
    macAddress VARCHAR(17) NOT NULL UNIQUE, 
    CONSTRAINT fk_Maquina_Aeroporto 
        FOREIGN KEY (fkAeroporto) REFERENCES Aeroporto(idAeroporto)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE Metrica (
    idMetrica INT PRIMARY KEY AUTO_INCREMENT,
    Metrica VARCHAR(50) NOT NULL , 
    unidadeMedida VARCHAR(10) NOT NULL
);

CREATE TABLE ParametroMonitoramento (
    fkMaquina INT NOT NULL,
    fkMetrica INT NOT NULL,
    limite DOUBLE NOT NULL,
    PRIMARY KEY (fkMaquina, fkMetrica),
    CONSTRAINT fk_Parametro_Maquina 
        FOREIGN KEY (fkMaquina) REFERENCES Maquina(idMaquina)
        ON DELETE CASCADE ON UPDATE CASCADE, 
    CONSTRAINT fk_Parametro_Metrica 
        FOREIGN KEY (fkMetrica) REFERENCES Metrica(idMetrica)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE HistoricoLeitura (
    idLeitura INT PRIMARY KEY AUTO_INCREMENT,
    fkMetricas INT NOT NULL,
    fkMaquina INT NOT NULL,
    valorCapturado FLOAT NOT NULL, 
    horario DATETIME NOT NULL,
    CONSTRAINT fk_Historico_Metrica 
        FOREIGN KEY (fkMetricas) REFERENCES Metrica(idMetrica)
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_Historico_Maquina 
        FOREIGN KEY (fkMaquina) REFERENCES Maquina(idMaquina)
        ON DELETE CASCADE ON UPDATE CASCADE 
);

-- 1. Inserir primeiro a localização
INSERT INTO Localizacao (estado, cidade, cep, numero) 
VALUES ('SP', 'Guarulhos', '07141970', 'S/N');

-- 2. Inserir o aeroporto utilizando o ID da localização inserida (assumindo que o ID gerado foi 1)
INSERT INTO Aeroporto (cnpj, nomeFantasia, email, telefone, nome, codigoIATA, fkLocalizacao, codigo) 
VALUES ('02575686000148', 'Aeroporto de Guarulhos', 'contato@gru.com.br', '1124452945', 'Aeroporto Internacional de São Paulo', 'GRU', 1, 'A0001');

-- Inserindo um usuário vinculado ao aeroporto de ID 1
INSERT INTO User (nome, cargo, email, senha, cpf, fkAeroportoUsuario) 
VALUES ('João Silva', 'Gerente de Operações', 'joao.silva@email.com', 'SenhaSegura123', '12345678901', 1);

-- Inserindo uma máquina vinculada ao usuário de ID 1
INSERT INTO Maquina (hostname, fkAeroporto, so, macAddress) 
VALUES ('DESKTOP-OP-01', 1, 'Windows 11 Pro', '00:1A:3F:F1:4C:C6');

INSERT INTO Metrica VALUES(default, 'cpu', '%'),
(default,'memoria', '%'),
(default,'disco', '%'),
(default,'Frequência da CPU', 'GHz'),
(default,'Memória Virtual', 'GB'),
(default,'Memória Total', 'GB'),
(default,'Memória Disponível', 'GB'),
(default,'Espaço em Disco', 'GB');

select * from historicoleitura;
select * from parametromonitoramento;
select * from metrica;

insert into maquina (idMaquina, hostname, fkAeroporto, so, macADdRESS) values
(default, 'nicolas', 1, 'Windows', '00:1A:3F:F1:4C:C7');
select * from maquina;
