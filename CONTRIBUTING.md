# Contribuindo com o Vórtice

A regra do projeto é simples: **uma feature deveria reduzir cliques, reduzir contexto que o usuário precisa lembrar ou reduzir a chance de fazer cagada**.

Antes de abrir um PR:

1. mantenha a interface simples;
2. evite transformar o Vórtice em uma IDE;
3. preserve compatibilidade com o estado em `%LOCALAPPDATA%\Vortice`;
4. não introduza telemetria sem discussão explícita;
5. não inclua segredos, tokens ou dados de projetos nos commits;
6. rode `python app.py --self-test`.

Issues pequenas e PRs focados são preferíveis a refactors gigantes.
