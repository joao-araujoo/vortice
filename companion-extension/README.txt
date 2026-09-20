VÓRTICE PUBLIC BETA — CHATGPT HANDOFF
=====================================

A distribuição pública do Vórtice usa o fluxo Assistido para o ChatGPT Web.
O Vórtice prepara o CONTEXTO.zip, o prompt e os anexos, abre o ChatGPT e mantém
Task/Rodada/histórico localmente. O envio e o retorno do arquivo ficam sob ação
do usuário.

Esta build pública não instala uma extensão que leia ou baixe automaticamente
respostas do ChatGPT Web. Isso deixa o pacote mais simples, previsível e adequado
para distribuição pública.

Fluxo:
1. COZINHAR CONTEXTO
2. MANDAR PRO GPT
3. confira/envie o prompt + arquivos no ChatGPT
4. baixe o ZIP final
5. arraste/selecione o ZIP no Vórtice e aplique

O Vórtice continua validando identidade de Task/Rodada, criando backup e
mantendo o histórico de aplicação.
