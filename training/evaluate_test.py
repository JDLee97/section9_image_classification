"""dataset/test 로 학습 완료 모델을 한 번 평가하는 추가 스크립트 (제공 코드 아님).

예: python -m training.evaluate_test --model artifacts/beans_resnet18

- 제공 training/finetune.py 는 Test 지표를 계산하지 않으므로 이 스크립트로 별도 측정한다.
- Test 결과는 모델·학습률 선택에 사용하지 않는다 (확정된 모델을 한 번만 평가).
- FastAPI/Redis를 거치지 않고 모델을 직접 불러 평가한다 (캐시·응답시간과 무관).
- 추가 패키지 없음: torch, transformers, pillow 만 사용 (requirements.txt 그대로).
"""
import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import AutoImageProcessor, AutoModelForImageClassification

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def collect_images(test_dir: Path, classes):
    items = []
    for label in classes:
        folder = test_dir / label
        if not folder.is_dir():
            raise SystemExit(f"폴더 없음: {folder}")
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in IMAGE_EXTS:
                items.append((path, label))
    if not items:
        raise SystemExit(f"Test 이미지 없음: {test_dir}")
    return items


def compute_metrics(rows, classes):
    total = len(rows)
    correct = sum(r["correct"] for r in rows)
    matrix = {gt: {pr: 0 for pr in classes} for gt in classes}
    for r in rows:
        matrix[r["gt"]][r["pred"]] += 1

    per_class = {}
    for c in classes:
        tp = matrix[c][c]
        fp = sum(matrix[g][c] for g in classes if g != c)
        fn = sum(matrix[c][p] for p in classes if p != c)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[c] = {
            "support": tp + fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    macro_f1 = sum(v["f1"] for v in per_class.values()) / len(classes)
    return {
        "test_count": total,
        "correct": correct,
        "test_accuracy": round(correct / total, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": {"rows=gt, cols=pred": classes,
                             "values": [[matrix[g][p] for p in classes] for g in classes]},
    }


def draw_confusion(metrics, classes, title, out_path: Path):
    values = metrics["confusion_matrix"]["values"]
    n = len(classes)
    cell, left, top = 120, 200, 110
    img = Image.new("RGB", (left + cell * n + 30, top + cell * n + 60), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    d.text((20, 15), title, fill="black", font=font)
    d.text((20, 35), f"Test Acc {metrics['test_accuracy']:.4f}  /  Macro F1 {metrics['macro_f1']:.4f}  /  n={metrics['test_count']}",
           fill="black", font=font)
    d.text((left + cell * n // 2 - 30, top - 40), "Predicted", fill="black", font=font)
    d.text((20, top + cell * n // 2), "True", fill="black", font=font)
    vmax = max(max(row) for row in values) or 1
    for j, c in enumerate(classes):
        d.text((left + j * cell + 8, top - 20), c[:17], fill="black", font=font)
    for i, gt in enumerate(classes):
        d.text((60, top + i * cell + cell // 2 - 6), gt[:17], fill="black", font=font)
        for j in range(n):
            v = values[i][j]
            shade = int(255 - 130 * v / vmax)
            color = (shade, shade, 255) if i == j else (255, shade, shade)
            x0, y0 = left + j * cell, top + i * cell
            d.rectangle([x0, y0, x0 + cell, y0 + cell], fill=color, outline="gray")
            d.text((x0 + cell // 2 - 8, y0 + cell // 2 - 6), str(v), fill="black", font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def draw_misclassified(rows, out_path: Path, max_items=24):
    wrong = [r for r in rows if not r["correct"]][:max_items]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    if not wrong:
        img = Image.new("RGB", (420, 60), "white")
        ImageDraw.Draw(img).text((10, 20), "No misclassified test images", fill="black", font=font)
        img.save(out_path)
        return 0
    thumb, cap, cols = 200, 46, 4
    rows_n = (len(wrong) + cols - 1) // cols
    img = Image.new("RGB", (cols * thumb, rows_n * (thumb + cap)), "white")
    d = ImageDraw.Draw(img)
    for k, r in enumerate(wrong):
        x, y = (k % cols) * thumb, (k // cols) * (thumb + cap)
        im = Image.open(r["path"]).convert("RGB")
        im.thumbnail((thumb, thumb))
        img.paste(im, (x, y))
        d.text((x + 4, y + thumb + 2), Path(r["path"]).name[:30], fill="black", font=font)
        d.text((x + 4, y + thumb + 16), f"GT : {r['gt']}", fill="green", font=font)
        d.text((x + 4, y + thumb + 30), f"PRED: {r['pred']} ({r['score']:.2f})", fill="red", font=font)
    img.save(out_path)
    return len(wrong)


def run(args):
    model_dir = Path(args.model)
    test_dir = Path(args.data) / "test"
    name = model_dir.name
    data_dir, fig_dir, ev_dir = Path(args.reports) / "data", Path(args.reports) / "figures", Path(args.reports) / "evidence"
    for d in (data_dir, fig_dir, ev_dir):
        d.mkdir(parents=True, exist_ok=True)

    processor = AutoImageProcessor.from_pretrained(str(model_dir))
    model = AutoModelForImageClassification.from_pretrained(str(model_dir))
    model.eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    classes = [id2label[i] for i in sorted(id2label)]

    items = collect_images(test_dir, classes)
    print(f"모델: {model_dir} / 클래스: {classes} / Test: {len(items)}장")

    rows = []
    started = time.perf_counter()
    for start in range(0, len(items), args.batch_size):
        batch = items[start:start + args.batch_size]
        images = [Image.open(p).convert("RGB") for p, _ in batch]
        inputs = processor(images=images, return_tensors="pt")
        with torch.no_grad():
            probs = model(**inputs).logits.softmax(dim=-1)
        for (path, gt), prob in zip(batch, probs):
            idx = int(prob.argmax())
            pred = id2label[idx]
            rows.append({
                "path": str(path), "gt": gt, "pred": pred,
                "score": float(prob[idx]), "correct": pred == gt,
                **{f"score_{c}": round(float(prob[i]), 6) for i, c in enumerate(classes)},
            })
    elapsed = time.perf_counter() - started

    metrics = compute_metrics(rows, classes)
    report = {
        "model": str(model_dir),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "test_dir": str(test_dir),
        "classes": classes,
        **metrics,
        "elapsed_sec": round(elapsed, 2),
        "note": "확정된 모델을 Test에서 1회 평가. Test는 모델·하이퍼파라미터 선택에 사용하지 않음.",
    }

    metrics_path = data_dir / f"test_metrics_{name}.json"
    metrics_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    pred_path = data_dir / f"test_predictions_{name}.csv"
    fields = ["path", "gt", "pred", "score", "correct"] + [f"score_{c}" for c in classes]
    with pred_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "score": round(r["score"], 6)})

    conf_path = fig_dir / f"test_confusion_{name}.png"
    draw_confusion(metrics, classes, f"Confusion matrix - {model_dir} (dataset/test)", conf_path)
    mis_path = fig_dir / f"test_misclassified_{name}.png"
    n_wrong = draw_misclassified(rows, mis_path)

    lines = [
        f"[{report['evaluated_at']}] model={model_dir} test={metrics['test_count']}",
        f"Test Accuracy = {metrics['test_accuracy']:.4f} ({metrics['correct']}/{metrics['test_count']})",
        f"Macro F1      = {metrics['macro_f1']:.4f}",
    ]
    for c, v in metrics["per_class"].items():
        lines.append(f"  {c:<18} P={v['precision']:.4f} R={v['recall']:.4f} F1={v['f1']:.4f} n={v['support']}")
    lines.append("Confusion (rows=GT, cols=PRED " + str(classes) + ")")
    for c, row in zip(classes, metrics["confusion_matrix"]["values"]):
        lines.append(f"  {c:<18} {row}")
    lines.append(f"오분류 {n_wrong}장 / 소요 {elapsed:.1f}s")
    lines += [f"저장: {p}" for p in (metrics_path, pred_path, conf_path, mis_path)]
    text = "\n".join(lines)
    print(text)
    (ev_dir / f"test_eval_log_{name}.txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="artifacts/beans_resnet18")
    parser.add_argument("--data", default="dataset")
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--batch-size", type=int, default=16)
    run(parser.parse_args())
