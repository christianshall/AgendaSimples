# Instruções - Agenda Individual por Barbeiro

## Mudanças Implementadas

### 1. Sistema de Agendas Individuais
- Cada barbeiro agora tem sua própria agenda individual
- Barbeiros só veem e gerenciam seus próprios agendamentos
- Clientes podem escolher qual barbeiro desejam ao agendar

### 2. Área Administrativa
- Administrador tem acesso a todas as agendas de todos os barbeiros
- Visualização consolidada de todos os agendamentos
- Administrador pode editar/excluir agendamentos de qualquer barbeiro

### 3. Modificações no Banco de Dados

**IMPORTANTE:** Execute o script SQL antes de usar o sistema:

```sql
-- Execute o arquivo: migracao_barbeiro_id.sql
```

Este script irá:
- Adicionar a coluna `barbeiro_id` na tabela `Clientes`
- Atribuir os agendamentos existentes ao primeiro barbeiro cadastrado
- Criar índices para melhorar a performance

### 4. Criar Usuário Administrador

Para criar um usuário administrador, execute no SQL Server:

```sql
USE [AgendaSimples];
GO

INSERT INTO usuarios (nome, email, senha, role)
VALUES ('Administrador', 'admin@barbearia.com', 'senha_admin', 'admin');
GO
```

**IMPORTANTE:** Altere o email e senha conforme necessário!

### 5. Estrutura de Roles

- **barbeiro**: Acesso apenas à própria agenda
- **admin**: Acesso a todas as agendas de todos os barbeiros

### 6. Funcionalidades por Perfil

#### Barbeiro:
- Visualiza apenas seus próprios agendamentos
- Pode criar, editar e excluir apenas seus agendamentos
- Exporta apenas seus agendamentos para Excel/PDF

#### Administrador:
- Visualiza todos os agendamentos de todos os barbeiros
- Pode criar, editar e excluir agendamentos de qualquer barbeiro
- Exporta todos os agendamentos para Excel/PDF
- Acessa através da rota `/admin_agenda`

#### Cliente:
- Ao agendar, deve selecionar qual barbeiro deseja
- O sistema verifica disponibilidade apenas para o barbeiro selecionado

### 7. Rotas Modificadas

- `/login` - Agora aceita role "admin" e redireciona adequadamente
- `/agenda` - Filtra agendamentos por barbeiro logado
- `/admin_agenda` - Nova rota para visualização administrativa
- `/agendar` - Associa agendamento ao barbeiro logado
- `/marcar` - Permite seleção de barbeiro pelo cliente
- `/editar` - Respeita permissões (barbeiro só edita os seus, admin edita todos)
- `/excluir` - Respeita permissões
- `/whatsapp` - Respeita permissões
- `/exportar_excel` - Filtra por barbeiro ou exporta tudo (admin)
- `/pdf_diario` - Filtra por barbeiro ou exporta tudo (admin)

### 8. Próximos Passos

1. Execute o script `migracao_barbeiro_id.sql` no SQL Server
2. Crie um usuário administrador no banco de dados
3. Teste o login como barbeiro e como administrador
4. Verifique se os agendamentos existentes foram atribuídos corretamente
5. Se necessário, ajuste manualmente os `barbeiro_id` dos agendamentos existentes

### 9. Notas Importantes

- Se houver agendamentos existentes sem `barbeiro_id`, eles serão atribuídos ao primeiro barbeiro cadastrado
- Recomenda-se revisar e ajustar manualmente os agendamentos existentes após a migração
- O campo `barbeiro_id` deve ser NOT NULL após a migração (descomente a linha no script SQL após garantir que todos os registros têm barbeiro_id)

### 10. Troubleshooting

**Problema:** Erro ao acessar agenda - "barbeiro_id não existe"
**Solução:** Execute o script de migração SQL

**Problema:** Agendamentos antigos não aparecem
**Solução:** Verifique se os agendamentos têm `barbeiro_id` atribuído

**Problema:** Admin não consegue ver todas as agendas
**Solução:** Verifique se o usuário tem role = 'admin' no banco de dados

