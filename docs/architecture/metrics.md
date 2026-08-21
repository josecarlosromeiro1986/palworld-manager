# Métricas do host

> Status: Implementado.

O Dashboard coleta CPU, memória, disco e contadores de rede com `psutil`. A rota autenticada `/dashboard/metrics` entrega um fragmento HTML atualizado pelo HTMX a cada 5 segundos por padrão. O intervalo operacional editável pelo painel aceita de 1 a 60 segundos, com validação obrigatória no backend. Chart.js é empacotado como asset local e exibe históricos separados de CPU/memória e tráfego recebido/enviado.

A vazão de rede é calculada pela diferença entre contadores consecutivos. A primeira amostra e reinicializações dos contadores produzem taxa zero, evitando valores negativos ou artificiais.

Somente CPU, memória e taxas de rede entram no buffer circular. A janela é limitada a 15 minutos em memória no processo web; disco aparece apenas como leitura atual. Nenhuma amostra é gravada no SQLite ou em arquivos, e o histórico recomeça vazio quando a aplicação é reiniciada.

Em produção nativa, as leituras representam o host Ubuntu onde o serviço web é executado. No ambiente Docker de desenvolvimento, as leituras refletem a visão de recursos disponível ao container.

Os limites operacionais de espaço livre começam em 20 GB para aviso e 10 GB para estado crítico. Ambos aceitam valores de 1 a 1024 GB, e o limite crítico deve permanecer estritamente menor que o limite de aviso. O fragmento de métricas aplica esses valores ao estado visual de disco; operações que exigem espaço continuam bloqueadas pelo limite crítico no backend.
