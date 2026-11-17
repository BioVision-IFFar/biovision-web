# Seguranca

- Nunca envie o arquivo `.env`, credenciais, dumps do banco, datasets ou modelos ao GitHub.
- Use `.env.example` apenas como lista das variaveis obrigatorias.
- Troque credenciais locais antes de publicar ou implantar a aplicacao.
- Restrinja o acesso ao MySQL e use HTTPS para o backend e para o servidor externo de imagens em producao.
- Relate vulnerabilidades de forma privada aos responsaveis pelo projeto.

## Mostra temporária

As proteções, os limites configuráveis, os testes locais e as instruções para
ativação estão em [docs/REVISAO-MOSTRA.md](docs/REVISAO-MOSTRA.md).

O backend serve uma lista explícita de arquivos públicos, valida uploads em
pastas temporárias privadas, utiliza uma fila única e limita pedidos por cookie
assinado e globalmente. Os resultados escapam textos do banco antes de gerar
HTML. Não há identificação de visitante baseada em `X-Forwarded-For`.

Esses controles não isolam os decodificadores do Windows nem substituem a
segurança das bibliotecas, da rede, do sistema operacional ou do túnel.
