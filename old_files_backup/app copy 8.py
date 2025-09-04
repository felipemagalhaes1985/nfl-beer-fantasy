import os
import json
from datetime import datetime, timedelta, timezone
from functools import wraps
import pytz # Importação da biblioteca pytz
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature 

print(f"Flask está carregando o arquivo: {os.path.abspath(__file__)}")

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Configuração da aplicação Flask
app = Flask(__name__)
# Adiciona enumerate e utcnow() (para uso em templates) aos globais do Jinja
app.jinja_env.globals.update(enumerate=enumerate, utcnow=lambda: datetime.now(timezone.utc)) 
app.config['SECRET_KEY'] = 'uma_chave_secreta_muito_segura_e_longa_para_o_projeto_nflbeer'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///nfl_beer_fantasy.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Chave secreta para os tokens de redefinição de senha (MUDE ESTA CHAVE EM PRODUÇÃO!)
app.config['SECURITY_PASSWORD_SALT'] = 'uma_nova_chave_secreta_para_redefinir_senha_muito_segura' 


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Define a rota para a página de login

# Define o fuso horário de Brasília uma vez para uso global
BRAZIL_TIMEZONE = pytz.timezone('America/Sao_Paulo')

# Instância do Serializer para tokens de segurança
s = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt=app.config['SECURITY_PASSWORD_SALT'])


# Adiciona o filtro datetime_local ao Jinja
@app.template_filter('datetime_local')
def _jinja2_filter_datetime_local(date_utc, tz):
    if not date_utc:
        return "N/A"
    # Converte de UTC para o timezone especificado
    return date_utc.astimezone(tz)

# --- Modelos de Banco de Dados ---
class Participante(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)
    # Relações
    palpites = db.relationship('Palpite', backref='participante', lazy=True, cascade="all, delete-orphan") 
    pontuacoes_rodada = db.relationship('PontuacaoRodada', backref='participante', lazy=True, cascade="all, delete-orphan")

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def __repr__(self):
        return f'<Participante {self.nome}>'

class Jogo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    id_externo = db.Column(db.String(100), unique=True, nullable=True) # ID externo para jogos importados
    semana = db.Column(db.Integer, nullable=False)
    data_hora = db.Column(db.DateTime, nullable=False) # Armazenado em UTC
    time1 = db.Column(db.String(100), nullable=False)
    time2 = db.Column(db.String(100), nullable=False)
    resultado = db.Column(db.String(20), nullable=True) # 'time1_vence', 'time2_vence', 'empate'
    placar_time1_final = db.Column(db.Integer, nullable=True)
    placar_time2_final = db.Column(db.Integer, nullable=True)
    # Relações
    palpites = db.relationship('Palpite', backref='jogo', lazy=True, cascade="all, delete-orphan") 
    
    def __repr__(self):
        return f'<Jogo {self.time1} vs {self.time2} - Semana {self.semana}>'

class Palpite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participante_id = db.Column(db.Integer, db.ForeignKey('participante.id'), nullable=False)
    jogo_id = db.Column(db.Integer, db.ForeignKey('jogo.id'), nullable=False)
    escolha = db.Column(db.String(20), nullable=False) # 'time1_vence', 'time2_vence', 'empate'
    placar_time1 = db.Column(db.Integer, nullable=False)
    placar_time2 = db.Column(db.Integer, nullable=False)
    pontuacao_recebida = db.Column(db.Integer, default=0) # Pontuação RAW (0-6)
    # Chave única para evitar múltiplos palpites do mesmo participante no mesmo jogo
    __table_args__ = (db.UniqueConstraint('participante_id', 'jogo_id', name='_participante_jogo_uc'),)

    def __repr__(self):
        return f'<Palpite {self.participante_id} no Jogo {self.jogo_id}>'

class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.String(200), nullable=False)

    def __repr__(self):
        return f'<Configuracao {self.chave}: {self.valor}>'

class PontuacaoRodada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participante_id = db.Column(db.Integer, db.ForeignKey('participante.id'), nullable=False)
    semana = db.Column(db.Integer, nullable=False)
    pontos_f1 = db.Column(db.Integer, nullable=False) # Pontos F1-like para a rodada
    __table_args__ = (db.UniqueConstraint('participante_id', 'semana', name='_participante_semana_uc'),)

    def __repr__(self):
        return f'<PontuacaoRodada Participante {self.participante_id} Semana {self.semana}: {self.pontos_f1} pontos>'

# --- Funções Auxiliares ---
@login_manager.user_loader
def load_user(participante_id):
    return Participante.query.get(int(participante_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Acesso não autorizado.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def is_prime_game(jogo):
    """
    Determina se um jogo é considerado "Prime Game" (TNF, SNF, MNF) para bônus de pontuação.
    Os horários são baseados em UTC, refletindo a madrugada seguinte ao horário de Brasília.
    """
    data_hora_utc = jogo.data_hora.replace(tzinfo=timezone.utc)
    
    dia_semana_utc = data_hora_utc.weekday() # Monday is 0, Sunday is 6
    hora_utc = data_hora_utc.hour

    # Horário de madrugada UTC (corresponde ao final da noite do dia anterior no Brasil)
    is_madrugada_utc = (hora_utc >= 0 and hora_utc <= 4) 

    if is_madrugada_utc and (dia_semana_utc == 4 or dia_semana_utc == 0 or dia_semana_utc == 1):
        return True
    return False

def get_config(key, default_value=None):
    config = Configuracao.query.filter_by(chave=key).first()
    if config:
        return config.valor
    return default_value

def set_config(key, value):
    config = Configuracao.query.filter_by(chave=key).first()
    if config:
        config.valor = value
    else:
        config = Configuracao(chave=key, valor=value)
        db.session.add(config)
    db.session.commit()


def calcular_e_atualizar_pontuacoes_jogo(jogo):
    """
    Calcula a pontuação RAW para todos os palpites de um jogo e atualiza no DB.
    """
    if jogo.resultado is None or jogo.placar_time1_final is None or jogo.placar_time2_final is None:
        # Se o resultado foi removido/zerado, zerar as pontuações RAW também
        palpites_do_jogo = Palpite.query.filter_by(jogo_id=jogo.id).all()
        for palpite in palpites_do_jogo:
            if palpite.pontuacao_recebida != 0:
                palpite.pontuacao_recebida = 0
                db.session.add(palpite)
        return False # Jogo ainda não tem resultado final, ou foi zerado

    palpites = Palpite.query.filter_by(jogo_id=jogo.id).all()
    prime_game = is_prime_game(jogo)

    for palpite in palpites:
        pontuacao_atual = 0

        # 1. Acertou o vencedor (ou empate)
        if palpite.escolha == jogo.resultado:
            pontuacao_atual += 1
            
            # Bônus de Prime Game (se acertou o vencedor e é prime)
            if prime_game:
                pontuacao_atual += 3 # +3 pontos extras para Prime Game

            # 2. Acertou o placar exato
            if palpite.placar_time1 == jogo.placar_time1_final and palpite.placar_time2 == jogo.placar_time2_final:
                pontuacao_atual += 1
            else:
                # 3. Acertou a diferença de pontos (se não acertou o placar exato, mas acertou o vencedor)
                palpite_diferenca = abs(palpite.placar_time1 - palpite.placar_time2)
                jogo_diferenca = abs(jogo.placar_time1_final - jogo.placar_time2_final)
                if palpite_diferenca == jogo_diferenca:
                    pontuacao_atual += 1
        
        # Atualiza a pontuação no palpite
        if palpite.pontuacao_recebida != pontuacao_atual:
            palpite.pontuacao_recebida = pontuacao_atual
            db.session.add(palpite) # Adiciona para commit

    return True # Não faz commit aqui, quem chamou essa função deve fazer o commit para agrupar as operações

def todos_jogos_semana_finalizados(semana):
    """
    Verifica se todos os jogos de uma determinada semana possuem resultado definido.
    """
    jogos_na_semana = Jogo.query.filter_by(semana=semana).all()
    if not jogos_na_semana:
        return False # Nao ha jogos nesta semana

    for jogo in jogos_na_semana:
        if jogo.resultado is None or jogo.placar_time1_final is None or jogo.placar_time2_final is None:
            return False # Encontrou um jogo sem resultado
    return True # Todos os jogos da semana tem resultado

def calcular_pontos_f1_por_rodada(semana):
    """
    Calcula e distribui os pontos F1-like para os participantes de uma rodada.
    """
    # Pontuação F1-like (para os 10 primeiros)
    pontos_f1_tabela = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

    # Obter a pontuação total RAW de cada participante para a semana
    pontuacoes_raw_por_participante = db.session.query(
        Participante.id,
        Participante.nome,
        db.func.sum(Palpite.pontuacao_recebida).label('total_pontos_raw')
    ).join(Palpite).join(Jogo).filter(
        Jogo.semana == semana
    ).group_by(Participante.id, Participante.nome).order_by(
        db.func.sum(Palpite.pontuacao_recebida).desc()
    ).all()

    # Zera as pontuações F1-like existentes para a semana antes de recalcular
    PontuacaoRodada.query.filter_by(semana=semana).delete()
    db.session.flush() # Aplica o delete antes de adicionar novos

    if pontuacoes_raw_por_participante:
        posicao_atual = 0
        ultima_pontuacao_raw = -1 # Garante que a primeira entrada tenha pontuação
        
        for i, (participante_id, nome, total_pontos_raw) in enumerate(pontuacoes_raw_por_participante):
            if total_pontos_raw == 0: # Não pontuou na rodada, não recebe F1 points
                break

            # Determina a posição para distribuição de pontos F1-like
            # Lida com empates (mesma pontuação RAW = mesma posição)
            if total_pontos_raw < ultima_pontuacao_raw:
                posicao_atual = i
            
            # Atribui pontos F1-like com base na posição
            if posicao_atual < len(pontos_f1_tabela):
                pontos_f1 = pontos_f1_tabela[posicao_atual]
            else:
                pontos_f1 = 0 # Fora do top N (ex: top 10)
            
            # Cria o registro de PontuacaoRodada (já zeramos antes, então é sempre novo)
            nova_pontuacao_rodada = PontuacaoRodada(
                participante_id=participante_id,
                semana=semana,
                pontos_f1=pontos_f1
            )
            db.session.add(nova_pontuacao_rodada)
            
            ultima_pontuacao_raw = total_pontos_raw
    
    # Commit é feito por quem chama a função, para agrupar operações

# Função para buscar dados de jogos externos (games.json)
def fetch_external_games_data():
    file_path = os.path.join(app.root_path, 'data', 'games.json')
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# --- Rotas da Aplicação ---
@app.route('/')
@app.route('/home')
def home():
    # Ranking Geral
    ranking_geral = db.session.query(
        Participante.id,
        Participante.nome,
        db.func.coalesce(db.func.sum(PontuacaoRodada.pontos_f1), 0).label('total_pontos_f1')
    ).outerjoin(PontuacaoRodada).group_by(Participante.id, Participante.nome).order_by(
        db.func.coalesce(db.func.sum(PontuacaoRodada.pontos_f1), 0).desc()
    ).all()
    
    # Pontuação por Rodada (para exibir o ranking de cada semana)
    pontuacoes_por_rodada = db.session.query(
        PontuacaoRodada.semana,
        Participante.nome,
        PontuacaoRodada.pontos_f1
    ).join(Participante).order_by(
        PontuacaoRodada.semana.desc(),
        PontuacaoRodada.pontos_f1.desc()
    ).all()

    # Agrupar pontuacoes_por_rodada por semana para facilitar a exibição
    pontuacoes_agrupadas = {}
    for p in pontuacoes_por_rodada:
        if p.semana not in pontuacoes_agrupadas:
            pontuacoes_agrupadas[p.semana] = []
        pontuacoes_agrupadas[p.semana].append({'nome': p.nome, 'pontos_f1': p.pontos_f1})

    # --- Tabela de Jogos da Semana Atual / Próximos Jogos ---
    # Lógica para determinar a "semana atual" ou a semana a ser exibida:
    # 1. Verifica se existe um override de semana definido pelo admin
    # 2. Se não houver override, encontra a menor semana que ainda possui jogos não finalizados.
    # 3. Se todos os jogos estiverem finalizados, exibir a maior semana existente.
    # 4. Se não houver jogos no DB, exibir Semana 1.
    
    semana_a_exibir = 1 # Valor padrão

    # 1. Verifica override
    semana_override_str = get_config('current_week_display_override')
    if semana_override_str and semana_override_str.isdigit():
        semana_a_exibir = int(semana_override_str)
    else:
        # 2. Se não houver override, usa a lógica de detecção automática
        earliest_unfinished_game = Jogo.query.filter(
            (Jogo.resultado == None) | (Jogo.placar_time1_final == None) | (Jogo.placar_time2_final == None)
        ).order_by(Jogo.semana.asc()).first()

        if earliest_unfinished_game:
            semana_a_exibir = earliest_unfinished_game.semana
        else: # Se todos os jogos estiverem finalizados, ou se não houver jogos
            latest_game = Jogo.query.order_by(Jogo.semana.desc()).first()
            if latest_game:
                semana_a_exibir = latest_game.semana
            # else: semana_a_exibir permanece 1 (padrão)

    jogos_semana_atual = Jogo.query.filter_by(semana=semana_a_exibir).order_by(Jogo.data_hora.asc()).all()


    return render_template('index.html', 
                           ranking_geral=ranking_geral, 
                           pontuacoes_agrupadas=pontuacoes_agrupadas,
                           jogos_semana_atual=jogos_semana_atual,
                           semana_atual_display=semana_a_exibir,
                           BRAZIL_TIMEZONE=BRAZIL_TIMEZONE) # Passa o fuso horário para o template


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        participante = Participante.query.filter_by(email=email).first()
        if participante and participante.check_password(senha):
            login_user(participante)
            flash('Login realizado com sucesso!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            flash('Email ou senha inválidos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('home'))

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        
        if Participante.query.filter_by(email=email).first():
            flash('Email já cadastrado.', 'danger')
        elif Participante.query.filter_by(nome=nome).first():
            flash('Nome de usuário já existe.', 'danger')
        else:
            novo_participante = Participante(nome=nome, email=email)
            novo_participante.set_password(senha)
            db.session.add(novo_participante)
            db.session.commit()
            flash('Cadastro realizado com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('registro.html')

# Rota para solicitar redefinição de senha
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email')
        participante = Participante.query.filter_by(email=email).first()

        if participante:
            # Gera um token com o ID do participante, com validade de 1 hora (3600 segundos)
            token = s.dumps(participante.id, salt='password-reset-salt')
            reset_url = url_for('reset_password', token=token, _external=True)

            # --- SIMULAÇÃO DE ENVIO DE E-MAIL ---
            # Em um ambiente real, você integraria um serviço de e-mail aqui (e.g., SendGrid, Mailgun)
            # Por enquanto, exibimos o link no console e em uma mensagem flash para facilitar o teste.
            print(f"DEBUG: Link de redefinição de senha para {participante.email}: {reset_url}")
            flash(f'Um link para redefinir sua senha foi enviado para o e-mail ({email}). Verifique sua caixa de entrada, incluindo a pasta de spam. (Link para desenvolvimento: {reset_url})', 'info')
            # --- FIM DA SIMULAÇÃO ---
        else:
            flash('Não encontramos uma conta com este e-mail.', 'danger')
        
        return redirect(url_for('login')) # Redireciona para login para evitar ataques de enumeração de e-mail

    return render_template('forgot_password.html')

# Rota para redefinir a senha usando o token
@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    try:
        # Carrega o ID do participante do token, com validade máxima de 1 hora
        user_id = s.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash('O link de redefinição de senha expirou. Por favor, solicite um novo.', 'danger')
        return redirect(url_for('forgot_password'))
    except BadTimeSignature:
        flash('O link de redefinição de senha é inválido. Por favor, solicite um novo.', 'danger')
        return redirect(url_for('forgot_password'))
    except Exception as e:
        flash(f'Ocorreu um erro com o link de redefinição: {e}. Por favor, solicite um novo.', 'danger')
        return redirect(url_for('forgot_password'))

    participante = Participante.query.get(user_id)
    if not participante:
        flash('Usuário associado ao link não encontrado.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirmar_senha = request.form.get('confirmar_senha')

        if not nova_senha or not confirmar_senha:
            flash('Por favor, preencha ambos os campos de senha.', 'danger')
            return render_template('reset_password.html', token=token)
        
        if nova_senha != confirmar_senha:
            flash('As senhas não coincidem.', 'danger')
            return render_template('reset_password.html', token=token)

        participante.set_password(nova_senha)
        db.session.commit()
        flash('Sua senha foi redefinida com sucesso! Você já pode fazer login.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)


@app.route('/meu_perfil')
@login_required
def meu_perfil():
    # Carrega os palpites do usuário logado, ordenados pela data do jogo
    palpites_do_usuario = Palpite.query.filter_by(participante_id=current_user.id).join(Jogo).order_by(Jogo.data_hora).all()
    
    # Calcula a pontuação total do usuário, usando coalesce para garantir 0 em vez de None
    total_pontos_f1 = db.session.query(
        db.func.coalesce(db.func.sum(PontuacaoRodada.pontos_f1), 0)
    ).filter_by(participante_id=current_user.id).scalar() 

    return render_template('meu_perfil.html', 
                           palpites=palpites_do_usuario,
                           total_pontos_f1=total_pontos_f1)


@app.route('/palpitar', methods=['GET', 'POST'])
@login_required
def palpitar():
    if request.method == 'POST':
        participante_id = current_user.id 
        jogo_id = request.form.get('jogo_id')
        escolha = request.form.get('escolha')
        placar_time1_str = request.form.get('placar_time1') 
        placar_time2_str = request.form.get('placar_time2') 

        if not jogo_id or not escolha or not placar_time1_str or not placar_time2_str:
            flash('Por favor, preencha todos os campos do palpite, incluindo os placares.', 'danger')
            return redirect(url_for('palpitar'))

        try:
            jogo = Jogo.query.get(jogo_id)

            if not jogo:
                flash('Jogo não encontrado.', 'danger')
                return redirect(url_for('palpitar'))
            
            # Validação para impedir palpites em jogos que já começaram ou terminaram
            if jogo.data_hora.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc): 
                flash('Não é possível palpitar em jogos que já começaram ou terminaram.', 'danger')
                return redirect(url_for('palpitar'))
            
            palpite_placar_time1 = int(placar_time1_str)
            palpite_placar_time2 = int(placar_time2_str)

            palpite_existente = Palpite.query.filter_by(
                participante_id=participante_id,
                jogo_id=jogo.id
            ).first()

            if palpite_existente:
                palpite_existente.escolha = escolha
                palpite_existente.placar_time1 = palpite_placar_time1
                palpite_existente.placar_time2 = palpite_placar_time2
                flash(f'Seu palpite para {jogo.time1} vs {jogo.time2} foi atualizado para: {escolha.replace("_", " ").title()} com placar {palpite_placar_time1}-{palpite_placar_time2}!', 'info')
            else:
                novo_palpite = Palpite(
                    participante_id=participante_id,
                    jogo_id=jogo.id,
                    escolha=escolha,
                    placar_time1=palpite_placar_time1, 
                    placar_time2=palpite_placar_time2  
                )
                db.session.add(novo_palpite)
                flash(f'Palpite para {jogo.time1} vs {jogo.time2} registrado: {escolha.replace("_", " ").title()} com placar {palpite_placar_time1}-{palpite_placar_time2}!', 'success')
            
            db.session.commit()

        except ValueError:
            flash('Placares devem ser números inteiros válidos.', 'danger')
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao registrar seu palpite: {e}', 'danger')
        
        return redirect(url_for('palpitar'))

    else:
        # Filtra para mostrar apenas jogos futuros na página de palpites
        jogos_disponiveis_para_palpite = Jogo.query.filter(
            Jogo.data_hora > datetime.now(timezone.utc) 
        ).order_by(Jogo.data_hora).all()
        
        for jogo in jogos_disponiveis_para_palpite:
            palpite = Palpite.query.filter_by(participante_id=current_user.id, jogo_id=jogo.id).first()
            jogo.meu_palpite = palpite 
            
        return render_template('palpitar.html', jogos=jogos_disponiveis_para_palpite)


@app.route('/admin_dashboard')
@login_required
@admin_required
def admin_dashboard():
    # Você pode adicionar dados do dashboard aqui, como número de usuários, jogos, etc.
    num_participantes = Participante.query.count()
    num_jogos = Jogo.query.count()
    num_palpites = Palpite.query.count()
    return render_template('admin_dashboard.html', 
                           num_participantes=num_participantes,
                           num_jogos=num_jogos,
                           num_palpites=num_palpites)

@app.route('/importar_jogos', methods=['POST'])
@login_required
@admin_required
def importar_jogos():
    success, message = importar_jogos_externos()
    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('admin_dashboard'))

def importar_jogos_externos():
    file_path = os.path.join(app.root_path, 'data', 'games.json')
    if not os.path.exists(file_path):
        return False, f"Arquivo {file_path} não encontrado na pasta data/. Certifique-se de que o arquivo existe e está no formato correto."
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            games_data = json.load(f)
    except json.JSONDecodeError:
        return False, 'Erro: O arquivo games.json não é um JSON válido. Verifique a sintaxe do JSON.'

    if not games_data:
        return False, "Nenhum dado de jogo para importar."

    jogos_adicionados = 0
    jogos_atualizados = 0
    erros = []

    try:
        for game_info in games_data:
            try:
                external_id = game_info.get('id_externo') 
                jogo_existente = None

                if external_id:
                    jogo_existente = Jogo.query.filter_by(id_externo=external_id).first()

                if jogo_existente:
                    jogo_existente.semana = game_info['semana']
                    # Garante que é datetime e converte para UTC
                    data_hora_local = datetime.fromisoformat(game_info['data_hora'])
                    jogo_existente.data_hora = BRAZIL_TIMEZONE.localize(data_hora_local).astimezone(timezone.utc)
                    jogo_existente.time1 = game_info['time1']
                    jogo_existente.time2 = game_info['time2']
                    # Resultado e placares só são atualizados se fornecidos e não são None
                    if 'resultado' in game_info and game_info['resultado'] is not None:
                        jogo_existente.resultado = game_info['resultado']
                    if 'placar_time1_final' in game_info and game_info['placar_time1_final'] is not None:
                        jogo_existente.placar_time1_final = game_info['placar_time1_final']
                    if 'placar_time2_final' in game_info and game_info['placar_time2_final'] is not None:
                        jogo_existente.placar_time2_final = game_info['placar_time2_final']
                    
                    db.session.add(jogo_existente)
                    jogos_atualizados += 1
                else:
                    novo_jogo = Jogo(
                        time1=game_info['time1'],
                        time2=game_info['time2'],
                        data_hora=BRAZIL_TIMEZONE.localize(datetime.fromisoformat(game_info['data_hora'])).astimezone(timezone.utc), # Garante que é datetime e converte para UTC
                        semana=game_info['semana'],
                        id_externo=external_id, 
                        resultado=game_info.get('resultado'), 
                        placar_time1_final=game_info.get('placar_time1_final'), 
                        placar_time2_final=game_info.get('placar_time2_final') 
                    )
                    db.session.add(novo_jogo)
                    jogos_adicionados += 1
                
            except Exception as e:
                identificador_jogo = game_info.get('id_externo') or f"{game_info.get('time1')} vs {game_info.get('time2')}"
                erros.append(f"Erro ao processar jogo {identificador_jogo}: {e}")

        db.session.commit()
        mensagem_sucesso = f"Importação concluída. {jogos_adicionados} jogos adicionados, {jogos_atualizados} jogos atualizados."
        if erros:
            mensagem_sucesso += f" Com {len(erros)} erros."
        return True, mensagem_sucesso
    except Exception as e:
        db.session.rollback()
        return False, f"Erro crítico ao finalizar a importação de jogos: {e}. Erros individuais: {erros}"


@app.route('/definir_resultado', methods=['GET', 'POST'])
@login_required
@admin_required
def definir_resultado():
    jogos_sem_resultado = Jogo.query.filter(
        (Jogo.resultado == None) | (Jogo.placar_time1_final == None) | (Jogo.placar_time2_final == None)
    ).order_by(Jogo.data_hora).all()

    if request.method == 'POST':
        jogo_id = request.form.get('jogo_id')
        resultado = request.form.get('resultado') # Nome do campo no HTML foi corrigido para 'resultado'
        placar_time1_final_str = request.form.get('placar_time1_final')
        placar_time2_final_str = request.form.get('placar_time2_final')

        if not jogo_id or not resultado or not placar_time1_final_str or not placar_time2_final_str:
            flash('Por favor, preencha todos os campos do resultado.', 'danger')
            return redirect(url_for('definir_resultado'))

        try:
            jogo = Jogo.query.get(jogo_id)
            if not jogo:
                flash('Jogo não encontrado.', 'danger')
                return redirect(url_for('definir_resultado'))

            placar_time1_final = int(placar_time1_final_str)
            placar_time2_final = int(placar_time2_final_str)

            jogo.resultado = resultado
            jogo.placar_time1_final = placar_time1_final
            jogo.placar_time2_final = placar_time2_final

            db.session.add(jogo) # Marca o jogo para ser salvo

            # Calcula e atualiza as pontuações RAW dos palpites para este jogo
            calcular_e_atualizar_pontuacoes_jogo(jogo)
            
            db.session.commit() # Salva as mudanças no jogo e nos palpites

            flash(f'Resultado para {jogo.time1} vs {jogo.time2} definido com sucesso!', 'success')

            # Verifica se todos os jogos da semana foram finalizados para calcular os pontos F1-like
            if todos_jogos_semana_finalizados(jogo.semana):
                flash(f"Todos os jogos da Semana {jogo.semana} foram finalizados. Calculando pontos F1-like para a rodada...", 'info')
                calcular_pontos_f1_por_rodada(jogo.semana)
                db.session.commit() # Salva os pontos F1-like

        except ValueError:
            flash('Os placares devem ser números inteiros válidos.', 'danger')
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao definir o resultado: {e}', 'danger')
        
        return redirect(url_for('definir_resultado'))
    
    return render_template('definir_resultado.html', jogos=jogos_sem_resultado)

# Rota para importar resultados de um arquivo JSON
@app.route('/importar_resultados_json', methods=['POST'])
@login_required
@admin_required
def importar_resultados_json_route():
    try:
        file_path = os.path.join(app.root_path, 'data', 'results_to_import.json')
        if not os.path.exists(file_path):
            flash(f'Arquivo {file_path} não encontrado na pasta data/. Certifique-se de que o arquivo existe e está no formato correto.', 'danger')
            return redirect(url_for('admin_dashboard'))

        with open(file_path, 'r', encoding='utf-8') as f:
            results_data = json.load(f)

        jogos_atualizados = 0
        semanas_afetadas = set() # Para controlar quais semanas precisam de cálculo F1-like
        
        for game_result in results_data:
            external_id = game_result.get('id_externo')
            if not external_id:
                flash(f"Erro: Jogo sem 'id_externo' no JSON de resultados. Pulando...", 'warning')
                continue

            jogo = Jogo.query.filter_by(id_externo=external_id).first()
            if not jogo:
                flash(f"Jogo com ID Externo '{external_id}' não encontrado no banco de dados. Pulando.", 'warning')
                continue

            # Atualiza os dados do jogo
            jogo.resultado = game_result.get('resultado')
            jogo.placar_time1_final = game_result.get('placar_time1_final')
            jogo.placar_time2_final = game_result.get('placar_time2_final')
            
            db.session.add(jogo) # Adiciona para o commit

            # Calcula e atualiza as pontuações individuais para este jogo
            calcular_e_atualizar_pontuacoes_jogo(jogo)
            
            # Adiciona a semana à lista de semanas afetadas
            semanas_afetadas.add(jogo.semana)
            jogos_atualizados += 1
        
        db.session.commit() # Salva as atualizações dos jogos e palpites

        # Após atualizar todos os jogos, verificar se alguma semana está completa para calcular F1-like
        for semana in semanas_afetadas:
            if todos_jogos_semana_finalizados(semana):
                flash(f"Todos os jogos da Semana {semana} finalizados. Recalculando pontos F1-like...", 'info')
                calcular_pontos_f1_por_rodada(semana)
                db.session.commit() # Salva os pontos F1-like

        flash(f'Importação de resultados concluída. {jogos_atualizados} jogos atualizados e pontuações recalculadas.', 'success')

    except FileNotFoundError:
        flash(f'Erro: Arquivo de resultados "{file_path}" não encontrado.', 'danger')
    except json.JSONDecodeError:
        flash('Erro: O arquivo de resultados não é um JSON válido. Verifique a sintaxe do JSON.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro inesperado durante a importação de resultados: {e}', 'danger')
    
    return redirect(url_for('admin_dashboard'))

# Rota para adicionar jogo manualmente
@app.route('/adicionar_jogo', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_jogo():
    if request.method == 'POST':
        time1 = request.form.get('time1')
        time2 = request.form.get('time2')
        data_hora_str = request.form.get('data_hora')
        semana_str = request.form.get('semana')
        id_externo = request.form.get('id_externo') # Opcional

        if not time1 or not time2 or not data_hora_str or not semana_str:
            flash('Por favor, preencha todos os campos obrigatórios (Times, Data/Hora, Semana).', 'danger')
            return redirect(url_for('adicionar_jogo'))
        
        try:
            # Converte data_hora de string para datetime
            # O input datetime-local do HTML retorna no formato 'YYYY-MM-DDTHH:MM'
            # Convertemos para UTC para manter a consistência no banco de dados
            data_hora_local = datetime.fromisoformat(data_hora_str)
            data_hora_utc = BRAZIL_TIMEZONE.localize(data_hora_local).astimezone(timezone.utc)
            
            semana = int(semana_str)

            # Verifica se já existe um jogo com o mesmo id_externo (se fornecido)
            if id_externo and Jogo.query.filter_by(id_externo=id_externo).first():
                flash(f'Já existe um jogo com o ID Externo "{id_externo}". Por favor, use um ID único ou deixe em branco.', 'danger')
                return redirect(url_for('adicionar_jogo'))
            
            novo_jogo = Jogo(
                time1=time1,
                time2=time2,
                data_hora=data_hora_utc,
                semana=semana,
                id_externo=id_externo if id_externo else None # Salva como None se vazio
            )
            db.session.add(novo_jogo)
            db.session.commit()
            flash(f'Jogo "{time1} vs {time2}" da Semana {semana} adicionado com sucesso!', 'success')
            return redirect(url_for('adicionar_jogo')) # Redireciona para o formulário limpo

        except ValueError:
            flash('Erro de formato: Certifique-se de que a semana é um número válido e a data/hora está correta.', 'danger')
            db.session.rollback()
        except Exception as e:
            flash(f'Ocorreu um erro ao adicionar o jogo: {e}', 'danger')
            db.session.rollback()

    return render_template('adicionar_jogo.html')

# --- Rota para Gerenciar Jogos (Ver/Editar/Excluir) ---
@app.route('/gerenciar_jogos', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_jogos():
    if request.method == 'POST':
        # Lógica para excluir jogo
        if 'delete_jogo' in request.form:
            jogo_id = request.form.get('jogo_id')
            jogo = Jogo.query.get(jogo_id)
            if jogo:
                try:
                    semana_do_jogo_excluido = jogo.semana # Guarda a semana para possível recálculo F1-like
                    
                    db.session.delete(jogo) # A cascata 'delete-orphan' no modelo Jogo cuidará dos palpites
                    db.session.commit()
                    flash(f'Jogo {jogo.time1} vs {jogo.time2} e seus palpites associados foram excluídos com sucesso.', 'success')

                    # Recalcular pontuações F1-like para a semana, caso necessário
                    if todos_jogos_semana_finalizados(semana_do_jogo_excluido):
                        calcular_pontos_f1_por_rodada(semana_do_jogo_excluido)
                        db.session.commit()
                        flash(f'Pontos F1-like para a Semana {semana_do_jogo_excluido} recalculados devido à exclusão.', 'info')
                    else:
                        pass 

                except Exception as e:
                    db.session.rollback()
                    flash(f'Erro ao excluir jogo: {e}', 'danger')
            else:
                flash('Jogo não encontrado para exclusão.', 'danger')
        
        return redirect(url_for('gerenciar_jogos'))
    else:
        # Lógica para exibir a lista de jogos
        jogos = Jogo.query.order_by(Jogo.semana.asc(), Jogo.data_hora.asc()).all()
        return render_template('gerenciar_jogos.html', jogos=jogos, BRAZIL_TIMEZONE=BRAZIL_TIMEZONE)

# --- Rota para Editar um Jogo Específico ---
@app.route('/editar_jogo/<int:jogo_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_jogo(jogo_id):
    jogo = Jogo.query.get(jogo_id)
    if not jogo:
        flash('Jogo não encontrado.', 'danger')
        return redirect(url_for('gerenciar_jogos'))

    if request.method == 'POST':
        time1 = request.form.get('time1')
        time2 = request.form.get('time2')
        data_hora_str = request.form.get('data_hora')
        semana_str = request.form.get('semana')
        id_externo = request.form.get('id_externo')
        resultado = request.form.get('resultado') 
        placar_time1_final_str = request.form.get('placar_time1_final')
        placar_time2_final_str = request.form.get('placar_time2_final')

        if not time1 or not time2 or not data_hora_str or not semana_str:
            flash('Por favor, preencha todos os campos obrigatórios (Times, Data/Hora, Semana).', 'danger')
            return redirect(url_for('editar_jogo', jogo_id=jogo_id))

        try:
            # Converte data_hora de string para datetime e UTC
            data_hora_local = datetime.fromisoformat(data_hora_str)
            data_hora_utc = BRAZIL_TIMEZONE.localize(data_hora_local).astimezone(timezone.utc)

            semana = int(semana_str)
            
            # Converte placares para int, se não estiverem vazios
            placar_time1_final = int(placar_time1_final_str) if placar_time1_final_str else None
            placar_time2_final = int(placar_time2_final_str) if placar_time2_final_str else None
            
            # Trata o 'None' vindo do select HTML para o campo resultado
            resultado_final = resultado if resultado != 'None' else None

            # Verifica se já existe um jogo com o mesmo id_externo (se fornecido e diferente do jogo atual)
            if id_externo and Jogo.query.filter(Jogo.id_externo == id_externo, Jogo.id != jogo_id).first():
                flash(f'Já existe um jogo com o ID Externo "{id_externo}". Por favor, use um ID único ou deixe em branco.', 'danger')
                return redirect(url_for('editar_jogo', jogo_id=jogo_id))

            # Guarda a semana antiga para possível recálculo F1-like
            semana_antiga = jogo.semana 

            jogo.time1 = time1
            jogo.time2 = time2
            jogo.data_hora = data_hora_utc
            jogo.semana = semana
            jogo.id_externo = id_externo if id_externo else None
            jogo.resultado = resultado_final
            jogo.placar_time1_final = placar_time1_final
            jogo.placar_time2_final = placar_time2_final
            
            db.session.add(jogo)

            # Recalcula as pontuações RAW dos palpites se o resultado ou placares finais foram alterados/definidos
            # E se o jogo agora tem um resultado válido
            if jogo.resultado is not None and jogo.placar_time1_final is not None and jogo.placar_time2_final is not None:
                calcular_e_atualizar_pontuacoes_jogo(jogo)
            else: # Se o resultado foi removido/zerado, zera as pontuações RAW dos palpites
                calcular_e_atualizar_pontuacoes_jogo(jogo) # Essa função já trata o caso de resultado None

            db.session.commit()
            flash(f'Jogo "{jogo.time1} vs {jogo.time2}" atualizado com sucesso!', 'success')
            
            # Recalcula pontos F1-like para a semana afetada (semana antiga e/ou nova)
            semanas_para_recalcular = {semana_antiga, jogo.semana}
            for s in semanas_para_recalcular:
                if s is not None and todos_jogos_semana_finalizados(s): # Garante que a semana é válida
                    calcular_pontos_f1_por_rodada(s)
                    db.session.commit() # Salva os pontos F1-like
                    flash(f'Pontos F1-like para a Semana {s} recalculados.', 'info')

            return redirect(url_for('gerenciar_jogos'))

        except ValueError:
            flash('Erro de formato: Certifique-se de que a semana e placares são números válidos e a data/hora está correta.', 'danger')
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao atualizar o jogo: {e}', 'danger')
        
        return render_template('editar_jogo.html', jogo=jogo)

    else: # GET request
        # Formata a data_hora para o formato datetime-local do HTML
        # Verifica se data_hora é datetime e não None antes de formatar
        if jogo.data_hora:
            data_hora_local = jogo.data_hora.astimezone(BRAZIL_TIMEZONE)
            jogo.data_hora_str = data_hora_local.strftime('%Y-%m-%dT%H:%M')
        else:
            jogo.data_hora_str = '' # Define como string vazia se data_hora for None
            
        return render_template('editar_jogo.html', jogo=jogo)


# --- Rotas de Gerenciamento de Participantes ---
@app.route('/gerenciar_participantes', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_participantes():
    if request.method == 'POST':
        if 'delete_participante' in request.form:
            participante_id = request.form.get('participante_id')
            participante = Participante.query.get(participante_id)
            if participante:
                try:
                    # O cascade="all, delete-orphan" nas relações Participante.palpites e Participante.pontuacoes_rodada
                    # já cuidará da exclusão dos palpites e pontuações de rodada associados.
                    db.session.delete(participante)
                    db.session.commit()
                    flash(f'Participante "{participante.nome}" e todos os seus dados associados foram excluídos com sucesso.', 'success')
                except Exception as e:
                    db.session.rollback()
                    flash(f'Erro ao excluir participante: {e}', 'danger')
            else:
                flash('Participante não encontrado para exclusão.', 'danger')
        return redirect(url_for('gerenciar_participantes'))
    else:
        participantes = Participante.query.order_by(Participante.nome.asc()).all()
        return render_template('gerenciar_participantes.html', participantes=participantes)

@app.route('/adicionar_participante_admin', methods=['GET', 'POST'])
@login_required
@admin_required
def adicionar_participante_admin():
    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        is_admin_str = request.form.get('is_admin')
        is_admin = True if is_admin_str == 'on' else False # Checkbox sends 'on' or None

        if not nome or not email or not senha:
            flash('Por favor, preencha todos os campos obrigatórios.', 'danger')
            return redirect(url_for('adicionar_participante_admin'))
        
        if Participante.query.filter_by(email=email).first():
            flash('Email já cadastrado.', 'danger')
            return redirect(url_for('adicionar_participante_admin'))
        
        if Participante.query.filter_by(nome=nome).first():
            flash('Nome de usuário já existe.', 'danger')
            return redirect(url_for('adicionar_participante_admin'))
        
        try:
            novo_participante = Participante(nome=nome, email=email, is_admin=is_admin)
            novo_participante.set_password(senha)
            db.session.add(novo_participante)
            db.session.commit()
            flash(f'Participante "{nome}" adicionado com sucesso!', 'success')
            return redirect(url_for('gerenciar_participantes')) # Redirect to list after adding
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao adicionar o participante: {e}', 'danger')
            
    return render_template('adicionar_participante_admin.html')

@app.route('/editar_participante/<int:participante_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_participante(participante_id):
    participante = Participante.query.get(participante_id)
    if not participante:
        flash('Participante não encontrado.', 'danger')
        return redirect(url_for('gerenciar_participantes'))

    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha') # Optional: Only update if provided
        is_admin_str = request.form.get('is_admin')
        is_admin = True if is_admin_str == 'on' else False

        # Basic validation
        if not nome or not email:
            flash('Nome e e-mail são campos obrigatórios.', 'danger')
            return redirect(url_for('editar_participante', participante_id=participante.id))
        
        # Check for uniqueness if email/name changed to avoid conflicts with other users
        if Participante.query.filter(Participante.email == email, Participante.id != participante_id).first():
            flash('Este e-mail já está sendo usado por outro participante.', 'danger')
            return redirect(url_for('editar_participante', participante_id=participante.id))
        
        if Participante.query.filter(Participante.nome == nome, Participante.id != participante_id).first():
            flash('Este nome de usuário já está sendo usado por outro participante.', 'danger')
            return redirect(url_for('editar_participante', participante_id=participante.id))
        
        try:
            participante.nome = nome
            participante.email = email
            participante.is_admin = is_admin
            
            if senha: # Only update password if a new one is provided
                participante.set_password(senha)
            
            db.session.add(participante)
            db.session.commit()
            flash(f'Participante "{participante.nome}" atualizado com sucesso!', 'success')
            return redirect(url_for('gerenciar_participantes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao atualizar o participante: {e}', 'danger')

    return render_template('editar_participante.html', participante=participante)

# --- Rota para Gerenciar Semana Atual ---
@app.route('/admin_config', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_config():
    current_override = get_config('current_week_display_override')
    
    if request.method == 'POST':
        semana_para_exibir_str = request.form.get('semana_para_exibir')
        
        if semana_para_exibir_str:
            try:
                semana_para_exibir = int(semana_para_exibir_str)
                if semana_para_exibir < 1:
                    flash('A semana deve ser um número positivo.', 'danger')
                else:
                    set_config('current_week_display_override', str(semana_para_exibir))
                    flash(f'Semana de exibição definida para a Semana {semana_para_exibir}.', 'success')
            except ValueError:
                flash('Valor inválido para semana. Por favor, insira um número.', 'danger')
        else: # Se o campo for deixado em branco, remove o override
            set_config('current_week_display_override', '') # Define como string vazia para remover o override
            flash('Semana de exibição automática reativada.', 'info')
            
        return redirect(url_for('admin_config'))

    return render_template('admin_config.html', current_override=current_override)

# --- Rota para Calcular Pontos F1 para uma Rodada Específica ---
@app.route('/calcular_f1_rodada', methods=['GET', 'POST'])
@login_required
@admin_required
def calcular_f1_rodada():
    if request.method == 'POST':
        semana_str = request.form.get('semana')
        if not semana_str:
            flash('Por favor, insira o número da semana.', 'danger')
            return redirect(url_for('calcular_f1_rodada'))
        
        try:
            semana = int(semana_str)
            if semana < 1:
                flash('O número da semana deve ser um valor positivo.', 'danger')
                return redirect(url_for('calcular_f1_rodada'))
            
            # Chama a função de cálculo
            calcular_pontos_f1_por_rodada(semana)
            db.session.commit() # Commit das alterações feitas no cálculo
            flash(f'Pontos F1-like para a Semana {semana} foram recalculados com sucesso!', 'success')
            
        except ValueError:
            flash('Número de semana inválido. Por favor, insira um número inteiro.', 'danger')
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao recalcular os pontos F1-like: {e}', 'danger')
            
        return redirect(url_for('calcular_f1_rodada'))
        
    return render_template('calcular_f1_rodada.html')

# --- NOVA Rota para a página de Regras ---
@app.route('/regras')
def regras():
    return render_template('regras.html')

# --- Execução da Aplicação ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Cria um usuário admin padrão se não existir
        if not Participante.query.filter_by(email='admin@example.com').first():
            admin_user = Participante(nome='admin', email='admin@example.com', is_admin=True)
            admin_user.set_password('admin_password_strong_!23')
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário admin padrão criado: admin@example.com / admin_password_strong_!23")
    app.run(debug=True)
