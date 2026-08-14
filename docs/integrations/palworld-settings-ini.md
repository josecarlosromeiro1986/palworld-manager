# Editor do PalWorldSettings.ini

> Status: Implementado com schema versionado, parser conservador, backup pré-save, fake e auditoria.

O painel **Configurações do Palworld** lê o caminho estrutural definido por `PALWORLD_SETTINGS`. Somente o ambiente `production` usa o filesystem real; development e test recebem um fake completo em memória e não acessam a instalação do Palworld.

## Definições versionadas

As definições ficam no código do projeto e registram a versão `1.0.3` da [documentação oficial de parâmetros](https://docs.palworldgame.com/settings-and-operation/configuration/). Não há scraping nem atualização automática em runtime.

O formulário apresenta apenas chaves presentes no arquivo e suportadas pelo schema local. Booleanos, inteiros, números, textos e enums são validados pelo backend. Limites numéricos específicos só são aplicados quando constam na referência oficial; portas usam o intervalo técnico de 1 a 65535.

As seguintes regras reduzem o risco de alterar uma configuração que ganhou semântica nova no Palworld:

- chaves ausentes não são inseridas automaticamente;
- chaves fora do schema são sinalizadas somente pelo nome, nunca editadas e preservadas;
- entradas que não possam ser interpretadas permanecem intactas;
- chaves conhecidas duplicadas ou com valor incompatível ficam bloqueadas para edição;
- estruturas compostas reconhecidas, como `CrossplayPlatforms`, permanecem somente leitura nesta versão;
- `AdminPassword` e `ServerPassword` são reconhecidas como sensíveis, mas seus valores não aparecem na interface nem entram na auditoria.

## Leitura e gravação conservadoras

O parser localiza exclusivamente `OptionSettings` dentro da seção `[/Script/Pal.PalGameWorldSettings]`, separa valores respeitando aspas e estruturas aninhadas e substitui apenas o trecho de valor de uma chave editada. Comentários, outras seções, ordem, espaços e entradas desconhecidas continuam como estavam.

Antes da gravação, o Manager compara o SHA-256 do conteúdo atual com a versão aberta pelo administrador. Uma alteração externa causa conflito e exige recarregar a página, evitando sobrescrever trabalho concorrente.

Quando existe alteração real, o adapter de produção:

1. recusa arquivo ausente, não regular, maior que 1 MiB, fora de UTF-8 ou acessado por symlink;
2. cria ao lado do INI uma cópia exata com nome `PalWorldSettings.ini.backup-<UTC>-<versão>` e modo `0600`;
3. grava um arquivo temporário no mesmo diretório, sincroniza seu conteúdo e preserva o modo do INI original;
4. substitui o alvo atomicamente e sincroniza o diretório.

Nenhuma retenção automática é aplicada a essas cópias pré-save nesta etapa. A instalação de produção deverá conceder ao usuário `palmanager` apenas as permissões necessárias no arquivo e diretório configurados, sem executar a aplicação como root e sem `sudo ALL`.

## Uso no Restore

O Restore do painel nunca grava diretamente o `PalWorldSettings.ini`
sanitizado do backup. Durante a pré-validação, o Manager restaura os campos não
sensíveis do backup e preserva exatamente os valores sensíveis do arquivo atual,
sem inventar defaults ou transformar valores sanitizados/vazios em
credenciais. Parâmetros desconhecidos continuam preservados pelas mesmas regras
conservadoras do editor.

O resultado combinado precisa ser válido e determinístico antes do Stop. Se o
arquivo atual estiver ausente, ilegível, inválido ou não puder ser combinado
com segurança, o Restore falha sem alterar o mundo. Logs e auditoria registram
somente a categoria segura, nunca conteúdo, valores ou paths estruturais.

## Interface, Restart e auditoria

Leitura e salvamento exigem sessão; a gravação também exige CSRF e o modal compartilhado. Após uma mudança, a página informa que o Restart é necessário e oferece o job de Restart já existente, com uma nova confirmação antes de executá-lo. Enquanto o job estiver pendente ou falhar, a ação permanece disponível; quando ele conclui com sucesso e o servidor volta online, o botão é removido e substituído pela confirmação de que as configurações foram aplicadas.

Cada tentativa de salvamento validada cria um `audit_event` `PALWORLD_SETTINGS_UPDATE`. O evento registra administrador, resultado, versão do schema, nomes das chaves alteradas e nome da cópia pré-save. Valores de configuração, conteúdo do arquivo, caminhos absolutos e senhas não são persistidos na auditoria.

Consulte também [Segurança](../architecture/security.md), [Controle do servidor](../operations/server-lifecycle.md) e a [especificação da V1](../../SPECIFICATION.md).
