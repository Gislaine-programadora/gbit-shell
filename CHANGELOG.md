# Changelog

## 1.0.1

- Adicionado `gbit vscode` para instalar a extensão sem abrir o shell interativo.
- Adicionado `gbit vscode --build` para gerar o VSIX sem instalar.
- Corrigida a documentação para diferenciar o launcher npm do pacote Python.

## 1.0.0 (revisado)

- **Tag limpo**: default "GBIT" (sem "-SHE").
- **Cor do tag**: azul VS Code `#0078d4` no lugar de roxo neon `#7c3aed`.
- **Prompt limpo**: removido `⬡ gbit-shell` (nome do projeto) depois do diretório.
- **Sem neon**: cyan neon → azul VS Code (#0078d4 / #38bdf8), violet neon → azul sólido (#3b82f6).
- **Menu de completion**: azul VS Code no lugar de roxo.
- **Scrollbar**: azul VS Code.
- **140 testes passando**.

## 1.0.0

Primeira release publica.

- Banner compacto: "gbit shell" numa linha so (sem ASCII art GBITS).
- Selo "node" desligado por padrao — prompt limpo ao abrir.
- Extensao VS Code com `overrideName: true` no perfil, entao a aba mostra
  "GBit Shell" no lugar de "python".
- Comandos da paleta registrados antes de tudo: se o perfil de terminal
  falhar (id em uso por instancia antiga), os comandos continuam funcionando.
- Comando "Verificar instalacao" mostra versao da extensao, perfil no
  settings.json e avisos de ativacao.
- Builtins `set` (com `--save` gravando no `~/.gbitrc`) e `disown`.
- Variaveis de ambiente GBIT_SHOW_NODE/GIT/VENV/TIME para ligar/desligar.
- Empacotador proprio do .vsix (`tools/build_vsix.py`), sem depender do
  vsce.

## 1.1.3

- Extensao do VS Code: os comandos da paleta agora sao registrados antes de
  qualquer outra coisa e cada registro e isolado. Antes, uma falha ao registrar
  o perfil de terminal (id ja em uso por uma instancia antiga da extensao)
  interrompia a ativacao e TODOS os comandos apareciam na paleta mas davam
  "comando nao encontrado" ao clicar.
- Cada comando passou a mostrar o erro real numa notificacao em vez de falhar
  em silencio.
- `GBit Shell: diagnostico` agora informa tambem a versao da extensao, se o
  perfil esta gravado no settings.json e eventuais avisos de ativacao.
- Aba do terminal aberta pelo botao `+`: o perfil gravado no settings.json usa
  `"overrideName": true`, o que faz a aba mostrar "GBit Shell" no lugar de
  "python".

## 1.1.2

- Builtins `set` (com `--save` gravando no `~/.gbitrc`) e `disown`.
- Variaveis de ambiente `GBIT_SHOW_NODE`, `GBIT_SHOW_GIT`, `GBIT_SHOW_VENV` e
  `GBIT_SHOW_TIME` para ligar/desligar partes do prompt.
- Selo do Node oculto por padrao no prompt.
- Empacotador proprio do `.vsix` (`tools/build_vsix.py`), sem depender do vsce.
