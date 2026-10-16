import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import fdb
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from fpdf import FPDF

app = Flask(__name__)
app.config["SECRET_KEY"] = "1234"

host = "localhost"
database = r"C:\Users\Aluno\Desktop\BANCO.FDB"
user = "SYSDBA"
password = "sysdba"

# ainda nao tem login, entao usamos sempre o usuario 1 (ele precisa existir na tabela USUARIO)
ID_USUARIO = 1

# valores da coluna TIPO
TIPO_DESPESA = 0
TIPO_RECEITA = 1

# valores da coluna CLASSIFICACAO
CLASSIFICACOES = {1: "Fixo", 2: "Variável"}

# chave do filtro: (texto do filtro, quantidade de dias para tras)
PERIODOS = {
    "ano": ("Último ano", 365),
    "mes": ("Último mês", 30),
    "semana": ("Últimos 7 dias", 7),
    "dia": ("Últimas 24 horas", 1)
}

COLUNAS = "ID_MOV_FINANCEIRA, DATA_LANCAMENTO, DESCRICAO, TIPO, VALOR, CLASSIFICACAO"


def conectar():
    return fdb.connect(host=host, database=database, user=user, password=password)


def consultar(sql, parametros=()):
    con = conectar()
    cursor = con.cursor()
    cursor.execute(sql, parametros)
    linhas = cursor.fetchall()
    con.close()
    return linhas


def executar(sql, parametros=()):
    con = conectar()
    cursor = con.cursor()
    cursor.execute(sql, parametros)
    con.commit()
    con.close()


def formatar_reais(valor):
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# deixa usar {{ valor|reais }} no html
app.add_template_filter(formatar_reais, "reais")


def pegar_periodo():
    periodo = request.args.get("periodo", "ano")
    if periodo not in PERIODOS:
        periodo = "ano"
    return periodo


def data_inicial(periodo):
    dias = PERIODOS[periodo][1]
    return date.today() - timedelta(days=dias)


def montar_movimentacao(linha):
    # a linha vem na ordem de COLUNAS
    receita = linha[3] == TIPO_RECEITA
    return {
        "id": linha[0],
        "data": linha[1].strftime("%d/%m"),
        "data_completa": linha[1].strftime("%d/%m/%Y"),
        "descricao": linha[2] or "",
        "categoria": "Receita" if receita else "Despesa",
        "classe": "receita" if receita else "despesa",
        "sinal": "+" if receita else "-",
        "classificacao": CLASSIFICACOES.get(linha[5], "-"),
        "valor": float(linha[4])
    }


def buscar_movimentacoes(data_inicio):
    linhas = consultar(
        f"SELECT {COLUNAS} FROM MOV_FINANCEIRA "
        "WHERE ID_USUARIO = ? AND DATA_LANCAMENTO >= ? "
        "ORDER BY DATA_LANCAMENTO DESC, ID_MOV_FINANCEIRA DESC",
        (ID_USUARIO, data_inicio)
    )
    movimentacoes = []
    for linha in linhas:
        movimentacoes.append(montar_movimentacao(linha))
    return movimentacoes


def buscar_movimentacao(id_mov):
    linhas = consultar(
        f"SELECT {COLUNAS} FROM MOV_FINANCEIRA "
        "WHERE ID_MOV_FINANCEIRA = ? AND ID_USUARIO = ?",
        (id_mov, ID_USUARIO)
    )
    if len(linhas) == 0:
        return None
    return montar_movimentacao(linhas[0])


# sem data_inicio soma tudo, desde o ano 2000
def somar(tipo, data_inicio=date(2000, 1, 1)):
    linhas = consultar(
        "SELECT COALESCE(SUM(VALOR), 0) FROM MOV_FINANCEIRA "
        "WHERE ID_USUARIO = ? AND TIPO = ? AND DATA_LANCAMENTO >= ?",
        (ID_USUARIO, tipo, data_inicio)
    )
    return float(linhas[0][0])


@app.route("/")
@app.route("/movimentacoes")
def inicio():
    periodo = pegar_periodo()
    dados = buscar_movimentacoes(data_inicial(periodo))
    saldo = somar(TIPO_RECEITA) - somar(TIPO_DESPESA)
    inicio_30_dias = date.today() - timedelta(days=30)
    receitas = somar(TIPO_RECEITA, inicio_30_dias)
    despesas = somar(TIPO_DESPESA, inicio_30_dias)
    return render_template(
        "index.html",
        dados=dados,
        saldo=saldo,
        receitas=receitas,
        despesas=despesas,
        periodo=periodo,
        periodos=PERIODOS
    )


@app.route("/nova-movimentacao", methods=["GET", "POST"])
def nova_movimentacao():
    if request.method == "GET":
        return render_template("nova_movimentacao.html")

    descricao = request.form.get("descricao", "").strip()
    categoria = request.form.get("categoria", "")
    classificacao = request.form.get("classificacao", "")

    try:
        valor = Decimal(request.form.get("valor", "").replace(",", "."))
        valor_ok = Decimal("0.01") <= valor < 1000000000
    except InvalidOperation:
        valor_ok = False

    if descricao == "":
        flash("Informe o tipo de movimentação.", "erro")
        return redirect(url_for("nova_movimentacao"))
    if categoria not in ("Receita", "Despesa"):
        flash("Selecione Receita ou Despesa.", "erro")
        return redirect(url_for("nova_movimentacao"))
    if classificacao not in ("1", "2"):
        flash("Selecione Fixo ou Variável.", "erro")
        return redirect(url_for("nova_movimentacao"))
    if not valor_ok:
        flash("Informe um valor válido maior que zero.", "erro")
        return redirect(url_for("nova_movimentacao"))

    if categoria == "Receita":
        tipo = TIPO_RECEITA
    else:
        tipo = TIPO_DESPESA

    executar(
        "INSERT INTO MOV_FINANCEIRA "
        "(TIPO, CLASSIFICACAO, VALOR, DATA_LANCAMENTO, DESCRICAO, ID_USUARIO) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tipo, int(classificacao), valor.quantize(Decimal("0.01")), date.today(), descricao, ID_USUARIO)
    )
    flash("Movimentação registrada com sucesso!", "sucesso")
    return redirect(url_for("inicio"))


@app.route("/movimentacao/<int:id_mov>")
def visualizar_movimentacao(id_mov):
    mov = buscar_movimentacao(id_mov)
    if mov is None:
        flash("Movimentação não encontrada.", "erro")
        return redirect(url_for("inicio"))
    return render_template("visualizar_movimentacao.html", mov=mov)


@app.route("/movimentacao/<int:id_mov>/editar", methods=["GET", "POST"])
def editar_movimentacao(id_mov):
    mov = buscar_movimentacao(id_mov)
    if mov is None:
        flash("Movimentação não encontrada.", "erro")
        return redirect(url_for("inicio"))

    if request.method == "GET":
        return render_template("editar_movimentacao.html", mov=mov)

    # so a descricao pode ser alterada
    descricao = request.form.get("descricao", "").strip()
    if descricao == "":
        flash("Informe a descrição da movimentação.", "erro")
        return redirect(url_for("editar_movimentacao", id_mov=id_mov))
    if len(descricao) > 100:
        flash("A descrição pode ter no máximo 100 caracteres.", "erro")
        return redirect(url_for("editar_movimentacao", id_mov=id_mov))

    executar(
        "UPDATE MOV_FINANCEIRA SET DESCRICAO = ? "
        "WHERE ID_MOV_FINANCEIRA = ? AND ID_USUARIO = ?",
        (descricao, id_mov, ID_USUARIO)
    )
    flash("Descrição atualizada com sucesso!", "sucesso")
    return redirect(url_for("visualizar_movimentacao", id_mov=id_mov))


@app.route("/movimentacao/<int:id_mov>/excluir", methods=["POST"])
def excluir_movimentacao(id_mov):
    mov = buscar_movimentacao(id_mov)
    if mov is None:
        flash("Movimentação não encontrada.", "erro")
        return redirect(url_for("inicio"))

    executar(
        "DELETE FROM MOV_FINANCEIRA WHERE ID_MOV_FINANCEIRA = ? AND ID_USUARIO = ?",
        (id_mov, ID_USUARIO)
    )
    flash("Movimentação excluída com sucesso!", "sucesso")
    return redirect(url_for("inicio"))


# o fpdf com fonte Arial so aceita latin-1
def texto_pdf(texto):
    return str(texto).encode("latin-1", "replace").decode("latin-1")


@app.route("/relatorio")
def relatorio():
    periodo = pegar_periodo()
    movimentacoes = buscar_movimentacoes(data_inicial(periodo))

    if not movimentacoes:
        return jsonify({"erro": "Nenhuma movimentação encontrada no período."}), 404

    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(190, 10, texto_pdf("Relatório de Movimentações - CredMais"), ln=True, align="C")

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    pdf.set_font("Arial", "", 10)
    pdf.cell(190, 8, texto_pdf(f"Período: {PERIODOS[periodo][0]}  |  Gerado em: {agora}"), ln=True, align="C")

    pdf.ln(5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Arial", "B", 10)
    pdf.cell(25, 10, "Data", border=1)
    pdf.cell(65, 10, texto_pdf("Descrição"), border=1)
    pdf.cell(25, 10, "Categoria", border=1)
    pdf.cell(30, 10, texto_pdf("Classificação"), border=1)
    pdf.cell(45, 10, "Valor (R$)", border=1, ln=True)

    pdf.set_font("Arial", "", 10)
    total_receitas = 0
    total_despesas = 0

    for item in movimentacoes:
        if item["categoria"] == "Receita":
            total_receitas += item["valor"]
        else:
            total_despesas += item["valor"]

        pdf.cell(25, 10, item["data_completa"], border=1)
        pdf.cell(65, 10, texto_pdf(item["descricao"])[:35], border=1)
        pdf.cell(25, 10, item["categoria"], border=1)
        pdf.cell(30, 10, texto_pdf(item["classificacao"]), border=1)
        pdf.cell(45, 10, f"{item['sinal']} {formatar_reais(item['valor'])}", border=1, ln=True)

    pdf.ln(10)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(190, 10, f"Total de movimentações: {len(movimentacoes)}", ln=True, align="C")
    pdf.cell(190, 10, f"Receitas: R$ {formatar_reais(total_receitas)}", ln=True, align="C")
    pdf.cell(190, 10, f"Despesas: R$ {formatar_reais(total_despesas)}", ln=True, align="C")
    pdf.cell(190, 10, f"Saldo do período: R$ {formatar_reais(total_receitas - total_despesas)}", ln=True, align="C")

    caminho = os.path.join(app.root_path, "relatorio_movimentacoes.pdf")
    pdf.output(caminho)
    return send_file(caminho, as_attachment=True, mimetype="application/pdf")


if __name__ == "__main__":
    app.run(debug=True)
