# Supervised Classification 方法速查

**用途**: 我们 mining 本质是 supervised binary classification — 已知 (features, up_won) pair, 学函数 f(features) → P(up_won=1). Rule mining 是其中最简单一种. 这文档列其它主流方法, 为将来 plan B 备查.

每个方法: **核心思想 + 公式直觉 + 例子 + 算力 + 何时用**.

---

## 0. 学科分类速览

```
AI (人工智能)
├── ML (Machine Learning, 机器学习)
│   ├── Supervised Learning ← 我们在这
│   │   ├── Classification (输出离散类别, 我们的 case)
│   │   └── Regression (输出连续值)
│   ├── Unsupervised Learning (无 label)
│   └── Reinforcement Learning (RL, agent+reward)
├── Symbolic AI (老派 rule-based, 我们的 rule mining 是这分支)
└── ... (NLP / CV / Robotics 是应用)
```

我们做的 = **Supervised Classification on event-aligned tabular features**.

---

## 1. Rule Mining (规则挖掘) — 我们目前用

**核心**: 枚举 `IF X op threshold THEN predict UP/DOWN` 形式的规则, 用 EV 评估.

**f 的形式**:
```
f(features) = 1 if (bn_taker_buy > 0.6) else 0
```

**例子**: factor `p_intra_60<0.445 & is_weekend==1` (H5) 就是一条规则.

**算力**: CPU 极轻, 280 features × 99 thresholds 几秒.

**优势**:
- 完全可解释 (每条规则人读得懂)
- 部署轻 (无 model file, 只有 expression string)
- audit 友好 (factor kill 知道 why)

**劣势**:
- 表达力弱 (只有 AND, 没有非线性)
- 错过 feature interaction (e.g. "A 和 B 的乘积" 表达不出)

---

## 2. Logistic Regression (LR, 逻辑回归)

**核心**: 给每个 feature 一个权重 (weight), 全部加起来过 sigmoid 函数压到 [0,1].

**f 的形式**:
```
P(up=1) = sigmoid(w₁·feature₁ + w₂·feature₂ + ... + bias)

sigmoid(x) = 1 / (1 + e^(-x))   # 把任意实数压到 (0, 1)
```

**例子**: 训练后可能得到:
```
P(up_won=1) = sigmoid(
    +0.30 × bn_taker_buy_ratio_60   # 正系数 → 高值利好 up
    -0.20 × funding_8h_now           # 负系数 → 高值利空 up
    +0.05 × is_weekend
    + ... 
    + (-0.1)                         # bias
)
```

**算力**: CPU 100ms 训完 24k 行.

**优势**: 系数可解释 ("哪个 feature 重要, 影响方向").

**劣势**: 只能拟合**线性关系**, BTC 市场常非线性.

**何时用**: baseline. 几乎所有 ML 项目第一步都先跑一遍 LR 看效果.

**库**: `sklearn.linear_model.LogisticRegression`

```python
from sklearn.linear_model import LogisticRegression
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)
preds = model.predict_proba(X_test)[:, 1]  # P(up=1) per row
weights = dict(zip(feature_names, model.coef_[0]))  # 看哪个 feature 权重高
```

---

## 3. Decision Tree (DT, 决策树)

**核心**: 一棵 IF-THEN 嵌套树. 每个内部节点是个 "feature op threshold" 判断, 叶节点是预测.

**f 的形式**:
```
IF bn_taker_buy_ratio_60 > 0.6:
    IF is_weekend == 1:
        return P(up)=0.70
    ELSE:
        return P(up)=0.55
ELSE:
    IF funding_8h_now > 0.0001:
        return P(up)=0.40
    ELSE:
        return P(up)=0.50
```

**算法找 threshold**: 在每个节点尝试所有 (feature, threshold) 组合, 选**information gain (信息增益)** 最大的 split. 即"split 后两边 P(up) 分布最不平衡".

**算力**: CPU 几秒.

**优势**:
- 自动发现 feature interaction (`A > X AND B < Y` 这种 conjunction)
- 自动找 threshold (不用手枚举 quantile)
- 跟我们 rule mining 思路接近 — **一条路径 (root → leaf) 就是一条复合 rule**

**劣势**: 单棵 tree **过拟合严重**, 不实用. 几乎都用 RF 或 GBT 集成.

**库**: `sklearn.tree.DecisionTreeClassifier`

---

## 4. Random Forest (RF, 随机森林)

**核心**: 建 100+ 棵 decision tree, 每棵看 random subset of (features, samples), 投票求平均.

**f 的形式**:
```
P(up=1) = (1/100) × Σ_{tree_i} tree_i.predict(features)
```

**为什么平均提升准确性**: 单棵 tree 有 high variance (随 data 改变结果剧烈变), ensemble 后 variance 大幅降低. 这叫**Bagging (Bootstrap Aggregating)**.

**算力**: CPU 30 秒训完 24k 行.

**优势**:
- 比 LR 强, 自动处理 non-linear + interaction
- 比单 tree 稳定
- 可输出 **feature importance** ("哪 10 个 feature 最重要")

**劣势**: 半黑盒. 知道哪些 feature 重要, 但不知道具体"为啥这次预测是 up".

**库**: `sklearn.ensemble.RandomForestClassifier`

```python
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(n_estimators=200, max_depth=8)
model.fit(X_train, y_train)
preds = model.predict_proba(X_test)[:, 1]
importance = dict(zip(feature_names, model.feature_importances_))
```

---

## 5. Gradient Boosting Trees (GBT) — LightGBM / XGBoost / CatBoost

**核心**: 跟 RF 一样建很多 tree, 但**树是序贯构建**: 第 n+1 棵 tree 专门**修正前 n 棵的预测误差**.

**算法直觉**:
```
1. 初始化: predict_0 = 全 0.5
2. for i in 1..N_trees:
       error_i = y_true - predict_{i-1}
       tree_i 学预测 error_i
       predict_i = predict_{i-1} + learning_rate × tree_i.predict(X)
```

**业界标杆**: **LightGBM** (Microsoft, 速度优), **XGBoost** (Tianqi Chen, 稳定优), **CatBoost** (Yandex, 类别 feature 优).

> Kaggle 竞赛过去 5 年 tabular 数据冠军 90% 用 LightGBM 或 XGBoost.

**算力**: CPU 30 秒 - 5 分钟. GPU 可选, 数据小没必要.

**优势**:
- 当前最强 tabular 分类器
- 处理 missing value (我们的 V1 OI/LS NaN 它自动处理)
- 有 regularization 防过拟合

**劣势**: 更黑盒, debug 难.

**库**: `lightgbm`

```python
import lightgbm as lgb
model = lgb.LGBMClassifier(
    n_estimators=500, learning_rate=0.05, max_depth=6,
    reg_alpha=0.1, reg_lambda=0.1,  # L1/L2 正则
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
preds = model.predict_proba(X_test)[:, 1]
importance = dict(zip(feature_names, model.feature_importances_))
```

---

## 6. MLP (Multi-Layer Perceptron, 多层感知机)

**核心**: 简单神经网络. 多层"加权求和 + 非线性激活" 串联.

**f 的形式**:
```
hidden_1 = ReLU(W_1 × input + b_1)              # input: shape (N, 280)
hidden_2 = ReLU(W_2 × hidden_1 + b_2)           # W_1: shape (128, 280)
output  = sigmoid(W_3 × hidden_2 + b_3)         # → P(up=1)

ReLU(x) = max(0, x)   # 非线性激活, 保持正值, 负值清零
```

**算力**: CPU 几分钟训. GPU 几十秒.

**优势**: 理论上**Universal Approximation Theorem (通用逼近定理)** — 足够 hidden units 能拟合任何连续函数.

**劣势**:
- 数据量小 (24k) 容易过拟合
- 完全黑盒
- 超参 (layer depth / width / learning rate) 调试痛苦

**库**: PyTorch / TensorFlow / Keras / `sklearn.neural_network.MLPClassifier`

```python
import torch
import torch.nn as nn

class SimpleNN(nn.Module):
    def __init__(self, n_features):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(n_features, 128), nn.ReLU(),
            nn.Linear(128, 64), nn.ReLU(),
            nn.Linear(64, 1), nn.Sigmoid()
        )
    def forward(self, x): return self.layers(x).squeeze()
```

**对我们**: 24k samples 太少, MLP 大概率 overfit. **不推荐, 除非加数据到 100k+**.

---

## 7. Transformer (注意力模型)

**核心**: GPT 那个架构. 输入是 sequence (e.g. 过去 100 个 candles 的 features), 每个位置用 attention 机制看历史相关位置.

**适用**: 时间序列, 文本, 任何 sequence 数据.

**对我们**: **杀鸡用牛刀**. 每个 event 独立分类问题, 不需要 sequence modeling.

**何时考虑**: 如果我们想从 "过去 30 个 candle 的 history" 学一个 model 预测下一个, 可以试. 但工程复杂度 +10×, 不推荐起步.

---

## Methods 对比矩阵

| 方法 | 可解释 | 训练算力 | 24k 数据是否过拟合 | 自动 interaction | 推荐场景 |
|---|---|---|---|---|---|
| Rule Mining (我们) | ✓✓ | 极轻 | 中 (multiple testing) | ✗ (手动 Phase B/C) | 起步, audit 关键 |
| Logistic Regression | ✓✓ | 极轻 | 低 | ✗ | baseline |
| Decision Tree | ✓ | 轻 | 高 (单 tree) | ✓ | 不直接用, RF/GBT 基础 |
| Random Forest | △ | 轻 | 低 | ✓ | rule mining 互补 |
| LightGBM | △ | 中 | 中 (需 regularize) | ✓ | 真正干活的, Kaggle 标配 |
| MLP (NN) | ✗ | 中 (CPU/GPU) | 高 | ✓ | 数据 100k+ 才考虑 |
| Transformer | ✗ | 重 (GPU) | 极高 | ✓✓ | sequence 任务 |

---

## 我们路线

```
Phase 1 (当前): Rule Mining 全开 Tier 1
  → 看 ceiling

Phase 2 (Plan B, 仅当 Phase 1 alpha 不够): 
  + LightGBM 找非线性 interaction
  + 把 LightGBM 找到的"高 importance feature 组合"翻译成 rule 验证

Phase 3 (远期, 加更多数据后):
  + 加 alt data (Coinbase / 经济日历 / on-chain)
  + 数据扩到 100k+ events 后, MLP/Transformer 才有意义
```

**Rule Mining 不被替代**, 它是**最后裁判**: ML 找到的任何 pattern, **必须翻译成 rule 后用 paper trade 验证才作数**.

---

## 自创算法的空间

教科书方法外, 量化常见自创方向:

1. **Custom feature engineering** (e.g. 我们的 `basis_*` 系列): 数学门槛低, 行业 alpha 主要来源
2. **Domain-specific rule grammar**: e.g. 加 `vwap`, `acceleration` 等业务算子
3. **Hybrid ensemble**: rule mining + LightGBM 投票
4. **Custom loss function**: 不优化 accuracy, 优化 gross_EV / Sharpe
5. **Bayesian methods**: 加 prior, 处理小样本下的不确定性

→ "novel ML" 不等于"新数学". 多数 novelty 来自**领域知识 + 工程组合**.
