# init_db.py
import os
from app import app, db, Participante # Importa o app, db e o modelo Participante

# Este script será executado no Render antes do Gunicorn.
# Ele usará as configurações de banco de dados definidas em app.py
# (que por sua vez busca DATABASE_URL das variáveis de ambiente do Render).

with app.app_context():
    print("Iniciando a criação/verificação de tabelas no banco de dados...")
    db.create_all() # Tenta criar as tabelas se não existirem no PostgreSQL
    print("Tabelas do banco de dados verificadas/criadas.")

    # Lógica para criar um usuário admin padrão, se ele ainda não existir
    if not Participante.query.filter_by(email='admin@example.com').first():
        print("Criando usuário admin padrão: admin@example.com / admin_password_strong_!23")
        admin_user = Participante(nome='admin', email='admin@example.com', is_admin=True)
        admin_user.set_password('admin_password_strong_!23')
        db.session.add(admin_user)
        db.session.commit() # Salva o novo usuário admin no banco
        print("Usuário admin padrão criado com sucesso!")
    else:
        print("Usuário admin padrão (admin@example.com) já existe. Pulando a criação.")

print("Script init_db.py concluído.")