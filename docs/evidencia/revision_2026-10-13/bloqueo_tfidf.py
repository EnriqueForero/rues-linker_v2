"""Experimento: bloqueo por TF-IDF de 3-gramas de caracteres + producto disperso por lotes, sobre el banco de 30k.
Mide tiempo, memoria, candidatos emitidos y recall de pares frente a la verdad (ID_GROUP).
No toca la librería: es evidencia para decidir si vale la pena cambiar L2."""
import gzip, time, sys, resource
import numpy as np, pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
import scipy.sparse as sp

df = pd.read_csv('data/benchmark/benchmark_institucional.csv.gz', dtype=str).fillna('')
n = len(df)
t0 = time.perf_counter()
nombres = df['RAZON_SOCIAL'].str.upper().str.replace(r'[^A-Z0-9 ]', ' ', regex=True).str.replace(r'\s+', ' ', regex=True).str.strip()
vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 3), min_df=2, dtype=np.float32)
X = normalize(vec.fit_transform(nombres)).tocsr()
t1 = time.perf_counter()
umbral = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
topk = int(sys.argv[2]) if len(sys.argv) > 2 else 30
pares_i, pares_j = [], []
lote = 2000
XT = X.T.tocsc()
for a in range(0, n, lote):
    S = (X[a:a+lote] @ XT).tocsr()            # similitud coseno dispersa del lote contra todos
    S.data[S.data < umbral] = 0; S.eliminate_zeros()
    for r in range(S.shape[0]):
        fila = S.getrow(r); i = a + r
        idx = fila.indices; val = fila.data
        if len(idx) > topk:                    # top-k por fila para acotar
            sel = np.argpartition(-val, topk)[:topk]; idx = idx[sel]
        idx = idx[idx > i]
        pares_i.append(np.full(len(idx), i, dtype=np.int64)); pares_j.append(idx.astype(np.int64))
pi = np.concatenate(pares_i); pj = np.concatenate(pares_j)
t2 = time.perf_counter()
# recall de pares verdaderos (mismo ID_GROUP)
g = df['ID_GROUP'].to_numpy()
orden = np.argsort(g, kind='stable'); gs = g[orden]
verdaderos = set()
ini = 0
for k in range(1, n + 1):
    if k == n or gs[k] != gs[ini]:
        miembros = orden[ini:k]
        if 1 < len(miembros) <= 2000:
            m = np.sort(miembros)
            ii, jj = np.triu_indices(len(m), 1)
            verdaderos.update(zip(m[ii].tolist(), m[jj].tolist()))
        ini = k
cand = set(zip(np.minimum(pi, pj).tolist(), np.maximum(pi, pj).tolist()))
hallados = len(verdaderos & cand)
reg = df['REGIMEN'].to_numpy()
por_reg = {}
for r in ('CON_ID','SIN_ID'):
    v = {q for q in verdaderos if reg[q[0]]==r and reg[q[1]]==r}
    por_reg[r] = (len(v), len(v & cand)/max(1,len(v)))
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f'n={n:,} umbral={umbral} topk={topk} | tfidf {t1-t0:.1f}s | pares {t2-t1:.1f}s | candidatos {len(cand):,} ({len(cand)/n:.1f}/registro) | '
      f'pares verdaderos {len(verdaderos):,} | recall de pares {hallados/len(verdaderos):.4f} | RSS máx {rss:.0f} MiB | por régimen {por_reg}')
