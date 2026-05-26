-- Execute no console SQL do Turso (ou libsql CLI).
-- Vincula usuarios.barbearia_id NULL por e-mail, agendamentos e tenant único.
-- Ajuste o último UPDATE se souber o ID exato da barbearia do Davi.

-- 1) Admin: mesmo e-mail da tabela barbearias
UPDATE usuarios
SET barbearia_id = (
    SELECT b.id FROM barbearias b
    WHERE LOWER(TRIM(b.email)) = LOWER(TRIM(usuarios.email))
    LIMIT 1
)
WHERE barbearia_id IS NULL
  AND role = 'admin'
  AND email IS NOT NULL;

-- 2) Profissional: e-mail igual ao da barbearia (conta compartilhada / legado)
UPDATE usuarios
SET barbearia_id = (
    SELECT b.id FROM barbearias b
    WHERE LOWER(TRIM(b.email)) = LOWER(TRIM(usuarios.email))
    LIMIT 1
)
WHERE barbearia_id IS NULL
  AND role IN ('barbeiro', 'profissional')
  AND email IS NOT NULL;

-- 3) Profissional: barbearia_id dos agendamentos em Clientes
UPDATE usuarios
SET barbearia_id = (
    SELECT c.barbearia_id FROM Clientes c
    WHERE c.barbeiro_id = usuarios.id
      AND c.barbearia_id IS NOT NULL
    LIMIT 1
)
WHERE barbearia_id IS NULL
  AND role IN ('barbeiro', 'profissional');

-- 4) Davi (ou outro nome): herda barbearia do único admin cadastrado
-- Substitua 1 pelo ID real da barbearia se preferir vínculo manual:
-- UPDATE usuarios SET barbearia_id = 1 WHERE LOWER(nome) LIKE '%davi%' AND barbearia_id IS NULL;

UPDATE usuarios
SET barbearia_id = (
    SELECT barbearia_id FROM usuarios
    WHERE role = 'admin' AND barbearia_id IS NOT NULL
    LIMIT 1
)
WHERE barbearia_id IS NULL
  AND role IN ('barbeiro', 'profissional');

-- Conferência
SELECT id, nome, email, role, barbearia_id FROM usuarios ORDER BY id;
