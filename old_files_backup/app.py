import os
import json
from datetime import datetime, timedelta, timezone
from functools import wraps
import pytz # Importação da biblioteca pytz

print(f"Flask está carregando o arquivo: {os.path.abspath(__file__)}")

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Configuração da aplicação Flask
app = Flask(__name__)
app.jinja_env.globals.update(enumerate=enumerate) # Garante que enumerate funcione nos templates
app.config['SECRET_KEY'] = 'uma_chave_secreta_muito_segura_e_longa_para_o_projeto_nflbeer'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///nfl_beer_fantasy.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Define a rota para a página de login

# Define o fuso horário de Brasília uma vez para uso global
BRAZIL_TIMEZONE = pytz.timezone('America/Sao_Paulo')

# --- Modelos de Banco de Dados ---
class Participante(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)
    # Relações
    palpites = db.relationship('Palpite', backref='participante', lazy=True)
    pontuacoes_rodada = db.relationship('PontuacaoRodada', backref='participante', lazy=True)

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
    palpites = db.relationship('Palpite', backref='jogo', lazy=True)

    def __repr__(self):
        return f'<Jogo {self.time1} vs {self.time2} - Semana {self.semana}>'

class Palpite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participante_id = db.Column(db.Integer, db.ForeignKey('participante.id'), nullable=False)
    jogo_id = db.Column(db.Integer, db.ForeignKey('jogo.id'), nullable=False)
    escolha = db.Column(db.String(20), nullable=False) # 'time1_vence', 'time2_vence', 'empate'
    placar_time1 = db.Column(db.Integer, nullable=False) # Corrigido para corresponder ao modelo
    placar_time2 = db.Column(db.Integer, nullable=False) # Corrigido para corresponder ao modelo
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

# Função is_prime_game corrigida
def is_prime_game(jogo):
    # Converte a data_hora do jogo (que é armazenada em UTC) para um objeto datetime aware
    data_hora_utc = jogo.data_hora.replace(tzinfo=timezone.utc)
    
    # Obtém o dia da semana e a hora em UTC
    dia_semana_utc = data_hora_utc.weekday() # Monday is 0, Sunday is 6
    hora_utc = data_hora_utc.hour

    # Critérios para Prime Game:
    # TNF: Quinta à noite no Brasil -> Sexta de madrugada UTC (dia 4)
    # SNF: Domingo à noite no Brasil -> Segunda de madrugada UTC (dia 0)
    # MNF: Segunda à noite no Brasil -> Terça de madrugada UTC (dia 1)
    
    # Horário de madrugada UTC (que corresponde ao final da noite do dia anterior no Brasil)
    # Ex: 00:00 a 04:00 UTC
    is_madrugada_utc = (hora_utc >= 0 and hora_utc <= 4) 

    # Verifica se o dia da semana UTC é um dos dias "prime" e está na faixa de horário da madrugada
    if is_madrugada_utc and (dia_semana_utc == 4 or dia_semana_utc == 0 or dia_semana_utc == 1):
        return True
    return False


def calcular_e_atualizar_pontuacoes_jogo(jogo):
    """
    Calcula a pontuação RAW para todos os palpites de um jogo e atualiza no DB.
    """
    if jogo.resultado is None or jogo.placar_time1_final is None or jogo.placar_time2_final is None:
        return False # Jogo ainda não tem resultado final

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

    # Não faz commit aqui, quem chamou essa função deve fazer o commit para agrupar as operações
    return True

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

    # Dicionário para armazenar a classificação e evitar recalcular pontuações para empates
    classificacao = []
    if pontuacoes_raw_por_participante:
        posicao_atual = 0
        ultima_pontuacao_raw = -1 # Garante que a primeira entrada tenha pontuação
        
        for i, (participante_id, nome, total_pontos_raw) in enumerate(pontuacoes_raw_por_participante):
            if total_pontos_raw == 0: # Não pontuou na rodada, não recebe F1 points
                break

            if total_pontos_raw < ultima_pontuacao_raw:
                posicao_atual = i
            
            # Se a posição atual estiver dentro do ranking F1-like
            if posicao_atual < len(pontos_f1_tabela):
                pontos_f1 = pontos_f1_tabela[posicao_atual]
            else:
                pontos_f1 = 0 # Fora do top 10
            
            # Verifica se já existe um registro de PontuacaoRodada para este participante e semana
            pontuacao_rodada_existente = PontuacaoRodada.query.filter_by(
                participante_id=participante_id,
                semana=semana
            ).first()

            if pontuacao_rodada_existente:
                if pontuacao_rodada_existente.pontos_f1 != pontos_f1:
                    pontuacao_rodada_existente.pontos_f1 = pontos_f1
                    db.session.add(pontuacao_rodada_existente)
            else:
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
        db.func.sum(PontuacaoRodada.pontos_f1).label('total_pontos_f1')
    ).outerjoin(PontuacaoRodada).group_by(Participante.id, Participante.nome).order_by(
        db.func.sum(PontuacaoRodada.pontos_f1).desc()
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

    return render_template('index.html', ranking_geral=ranking_geral, pontuacoes_agrupadas=pontuacoes_agrupadas)

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

@app.route('/forgot_password')
def forgot_password():
    # Rota adicionada para resolver o BuildError.
    # Implemente a lógica de redefinição de senha aqui quando precisar.
    # Por enquanto, apenas renderiza uma página de placeholder ou redireciona.
    flash('Funcionalidade de redefinição de senha ainda não implementada. Por favor, contate o administrador.', 'info')
    # Se você tem um forgot_password.html, use: return render_template('forgot_password.html')
    return redirect(url_for('login')) # Redireciona de volta para o login

@app.route('/meu_perfil')
@login_required
def meu_perfil():
    # Carrega os palpites do usuário logado, ordenados pela data do jogo
    palpites_do_usuario = Palpite.query.filter_by(participante_id=current_user.id).join(Jogo).order_by(Jogo.data_hora).all()
    return render_template('meu_perfil.html', palpites=palpites_do_usuario)


@app.route('/palpitar', methods=['GET', 'POST'])
@login_required
def palpitar():
    if request.method == 'POST':
        participante_id = current_user.id 
        jogo_id = request.form.get('jogo_id')
        escolha = request.form.get('escolha')
        palpite_placar_time1_str = request.form.get('placar_time1') 
        palpite_placar_time2_str = request.form.get('placar_time2') 

        if not jogo_id or not escolha or not palpite_placar_time1_str or not palpite_placar_time2_str:
            flash('Por favor, preencha todos os campos do palpite, incluindo os placares.', 'danger')
            return redirect(url_for('palpitar'))

        try:
            jogo = Jogo.query.get(jogo_id)

            if not jogo:
                flash('Jogo não encontrado.', 'danger')
                return redirect(url_for('palpitar'))
            
            # Restaura a validação para impedir palpites em jogos que já começaram ou terminaram
            if jogo.data_hora.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc): 
                flash('Não é possível palpitar em jogos que já começaram ou terminaram.', 'danger')
                return redirect(url_for('palpitar'))
            
            palpite_placar_time1 = int(palpite_placar_time1_str)
            palpite_placar_time2 = int(palpite_placar_time2_str)

            palpite_existente = Palpite.query.filter_by(
                participante_id=participante_id,
                jogo_id=jogo.id
            ).first()

            if palpite_existente:
                # --- CORREÇÃO AQUI: usando 'placar_time1' e 'placar_time2' diretamente ---
                palpite_existente.escolha = escolha
                palpite_existente.placar_time1 = palpite_placar_time1  # CORRIGIDO
                palpite_existente.placar_time2 = palpite_placar_time2  # CORRIGIDO
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
        # Restaura o filtro para mostrar apenas jogos futuros na página de palpites
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
    games_data = fetch_external_games_data()
    
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
                    jogo_existente.data_hora = datetime.fromisoformat(game_info['data_hora']) # Garante que é datetime
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
                        data_hora=datetime.fromisoformat(game_info['data_hora']), # Garante que é datetime
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
        resultado = request.form.get('resultado')
        placar_time1_final = request.form.get('placar_time1_final')
        placar_time2_final = request.form.get('placar_time2_final')

        if not jogo_id or not resultado or not placar_time1_final or not placar_time2_final:
            flash('Por favor, preencha todos os campos do resultado.', 'danger')
            return redirect(url_for('definir_resultado'))

        try:
            jogo = Jogo.query.get(jogo_id)
            if not jogo:
                flash('Jogo não encontrado.', 'danger')
                return redirect(url_for('definir_resultado'))

            placar_time1_final = int(placar_time1_final)
            placar_time2_final = int(placar_time2_final)

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

# Nova rota para importar resultados de um arquivo JSON
@app.route('/importar_resultados_json', methods=['POST'])
@login_required
@admin_required
def importar_resultados_json_route():
    try:
        file_path = os.path.join(app.root_path, 'data', 'results_to_import.json')
        if not os.path.exists(file_path):
            flash(f'Arquivo {file_path} não encontrado.', 'danger')
            return redirect(url_for('admin_dashboard'))

        with open(file_path, 'r', encoding='utf-8') as f:
            results_data = json.load(f)

        jogos_atualizados = 0
        semanas_com_jogos_finalizados = set() # Para controlar quais semanas precisam de cálculo F1-like
        
        for game_result in results_data:
            external_id = game_result.get('id_externo')
            if not external_id:
                flash(f"Erro: Jogo sem 'id_externo' no JSON de resultados. Pulando...", 'warning')
                continue

            jogo = Jogo.query.filter_by(id_externo=external_id).first()
            if not jogo:
                flash(f"Jogo com ID Externo '{external_id}' não encontrado no banco de dados.", 'warning')
                continue

            # Atualiza os dados do jogo
            jogo.resultado = game_result.get('resultado')
            jogo.placar_time1_final = game_result.get('placar_time1_final')
            jogo.placar_time2_final = game_result.get('placar_time2_final')
            
            db.session.add(jogo) # Adiciona para o commit

            # Calcula e atualiza as pontuações individuais para este jogo
            calcular_e_atualizar_pontuacoes_jogo(jogo)
            
            # Marca a semana para verificar o cálculo F1-like depois
            semanas_com_jogos_finalizados.add(jogo.semana)
            jogos_atualizados += 1
        
        db.session.commit() # Salva as atualizações dos jogos e palpites

        # Após atualizar todos os jogos, verificar se alguma semana está completa para calcular F1-like
        for semana in semanas_com_jogos_finalizados:
            if todos_jogos_semana_finalizados(semana):
                flash(f"Todos os jogos da Semana {semana} finalizados. Calculando pontos F1-like...", 'info')
                calcular_pontos_f1_por_rodada(semana)
                db.session.commit() # Salva os pontos F1-like

        flash(f'Importação de resultados concluída. {jogos_atualizados} jogos atualizados e pontuações recalculadas.', 'success')

    except FileNotFoundError:
        flash(f'Erro: Arquivo de resultados "{file_path}" não encontrado.', 'danger')
    except json.JSONDecodeError:
        flash('Erro: O arquivo de resultados não é um JSON válido.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro inesperado durante a importação de resultados: {e}', 'danger')
    
    return redirect(url_for('admin_dashboard'))


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