import json
import random
import sys

ORC = json.load(open("oracle.json"))
F = json.load(open("../new/flags.json"))
S = json.load(open("split.json"))


def rows(toks, col=0):
    out = []
    for n in toks:
        rules = {f"{h.lower()}:{i}" for h, i in F[n]["flagged_swaps"]}
        for k, lab in ORC[n]["swaps"].items():
            out.append((n, k.split(":")[0], lab[col], k in rules, F[n]["trade"].get(k, 0.0)))
    return out


def prf(r, pred):
    tp = sum(1 for x in r if x[2] and pred(x))
    fp = sum(1 for x in r if not x[2] and pred(x))
    fn = sum(1 for x in r if x[2] and not pred(x))
    return (
        tp / (tp + fp) if tp + fp else float("nan"),
        tp / (tp + fn) if tp + fn else float("nan"),
        tp + fp,
    )


def boot(r, pred, B=1000):
    by = {}
    for x in r:
        by.setdefault(x[1], []).append(x)
    keys = list(by)
    ps, rs = [], []
    rng = random.Random(0)
    for _ in range(B):
        s = [x for k in rng.choices(keys, k=len(keys)) for x in by[k]]
        p, rc, _ = prf(s, pred)
        ps.append(p)
        rs.append(rc)
    ps, rs = (sorted(z for z in v if z == z) for v in (ps, rs))

    def ci(v):
        return v[int(0.025 * len(v))], v[int(0.975 * len(v)) - 1]

    return ci(ps), ci(rs)


if __name__ == "__main__":
    part = sys.argv[1]
    col = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    r = rows(S[part], col)
    tcut = F["aave"]["tcut"]
    print(part, "swaps", len(r), "wash", sum(x[2] for x in r))
    for name, pred in [
        ("rules", lambda x: x[3]),
        ("model", lambda x: x[4] >= tcut),
        ("rules or model", lambda x: x[3] or x[4] >= tcut),
        ("all", lambda x: True),
    ]:
        p, rc, k = prf(r, pred)
        (pl, ph), (rl, rh) = boot(r, pred)
        print(
            f"{name:15s} flagged {k:5d} P {p:.3f} [{pl:.3f},{ph:.3f}] R {rc:.3f} [{rl:.3f},{rh:.3f}]"
        )
