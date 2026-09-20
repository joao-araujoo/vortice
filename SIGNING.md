# Assinatura do executável

A Release pública já é montada de forma reproduzível pelo GitHub Actions, mas **não existe assinatura de código confiável sem um certificado real**.

Sem assinatura, o Windows SmartScreen pode exibir um aviso principalmente nas primeiras versões/downloads. Isso não significa automaticamente que o arquivo contém malware; é um problema comum de reputação de binários novos, mas o usuário deve sempre conferir a origem e o SHA-256.

Para uma distribuição mais madura:

1. obtenha um certificado Authenticode/code-signing confiável, ou candidate o projeto a um serviço de assinatura para open source;
2. assine `Vortice.exe` e `Vortice-Setup.exe` no workflow;
3. mantenha `SHA256SUMS.txt` nas Releases;
4. nunca recomende desativar Defender/SmartScreen para instalar o Vórtice.

O workflow atual foi mantido sem uma assinatura falsa/self-signed porque ela não resolveria a confiança pública do Windows.
