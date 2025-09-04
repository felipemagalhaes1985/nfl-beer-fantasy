# create_admin.py

from app import app, db, Participante # Importa o seu app, db e o modelo Participante

# --- Informações do seu novo administrador ---
ADMIN_NAME = 'admin'        # Nome de usuário para o admin
ADMIN_EMAIL = 'admin@example.com' # Email do admin
ADMIN_PASSWORD = 'admin123' # **MUDA ESTA SENHA PARA ALGO SEGURO!**

# --- Script para criar o administrador ---
with app.app_context():
    print(f"Verificando se o usuário administrador '{ADMIN_NAME}' já existe...")
    existing_admin = Participante.query.filter_by(email=ADMIN_EMAIL).first()

    if existing_admin:
        print(f"Administrador com email '{ADMIN_EMAIL}' já existe. Atualizando (se necessário)...")
        # Você pode atualizar a senha ou outras propriedades aqui se quiser
        existing_admin.set_password(ADMIN_PASSWORD)
        existing_admin.is_admin = True
        db.session.commit()
        print(f"Administrador '{ADMIN_NAME}' atualizado com sucesso!")
    else:
        print(f"Criando novo usuário administrador '{ADMIN_NAME}'...")
        new_admin = Participante(nome=ADMIN_NAME, email=ADMIN_EMAIL, is_admin=True)
        new_admin.set_password(ADMIN_PASSWORD) # Define a senha usando o método seguro
        
        db.session.add(new_admin)
        db.session.commit()
        print(f"Administrador '{ADMIN_NAME}' criado com sucesso! Email: {ADMIN_EMAIL}, Senha: {ADMIN_PASSWORD}")
    
    print("\nVerificando todos os administradores:")
    all_admins = Participante.query.filter_by(is_admin=True).all()
    for admin_user in all_admins:
        print(f"  - ID: {admin_user.id}, Nome: {admin_user.nome}, Email: {admin_user.email}, Admin: {admin_user.is_admin}")
