# Multi-Agent Retail Simulation — Presentation Outline

## Slide 1 — Title
- Multi-Agent Retail Simulation
- Author: (Your Name)
- Date

## Slide 2 — One-line Purpose
- Evaluate pricing, inventory, marketing and returns policies using model-driven simulation.

## Slide 3 — Project Structure (Files & Folders)
- generate_data.py — synthetic data generator
- schema.sql — database schema
- agents/ — agent implementations (forecasting, pricing, inventory, marketing, returns)
- simulate.py — core simulation loop
- dashboard/app.py — visualization app
- Notebooks/ — model training & experiments

## Slide 4 — Data (What we used)
- Source: synthetic data produced by `generate_data.py` (or real ingestion if used)
- Tables in `schema.sql`: sales, inventory, returns, prices, events, metrics
- Stored datasets: `data/` folder
- Feature lists: `models/demand_model_features.txt`, `models/returns_model_features.txt`

## Slide 5 — Data Quality & Preprocessing
- Typical issues: missing timestamps, sparse SKUs, noisy transactions
- Preprocessing steps:
  - Imputation (time-series forward/backfill for features)
  - Aggregation to daily SKU-level demand
  - Outlier removal and clipping
  - Feature scaling/encoding
- How "waste" was addressed (data cleaning, synthetic augmentation)

## Slide 6 — Feature Engineering (Demand)
- Time features: day-of-week, month, holidays
- Price features: current price, historical mean price, promotion flags
- Inventory features: stock level, days-of-supply
- Marketing features: campaign intensity, channel spend
- Lag features and rolling statistics (7/14/28-day demand lags)

## Slide 7 — Demand Model — Overview
- Model type: (e.g., Gradient Boosting / XGBoost / LightGBM / RandomForest or Neural Network)
- Input features: list from `models/demand_model_features.txt`
- Target: next-period demand (units)
- Loss / metrics: MAE, RMSE, MAPE

## Slide 8 — Demand Model — Training Process
- Train/val/test split: temporal split (train historic → validate recent → test latest)
- Cross-validation: time-series CV (rolling-window)
- Hyperparameter tuning: grid/random search or Optuna
- Handling imbalance and zero-inflation: count-transform or Poisson modeling
- Regularization and early stopping (for GBMs/NNs)

## Slide 9 — Demand Model — Evaluation
- Key metrics: MAE, RMSE, MAPE per SKU and aggregated
- Calibration plots: predicted vs actual, residual histograms
- Business translation: forecast error → stockouts / overstock impact

## Slide 10 — Returns Model — Overview
- Model type: classification/regression depending on problem (return likelihood or return lead-time)
- Input features: from `models/returns_model_features.txt`
- Target: binary return flag or return delay (days)
- Metrics: ROC-AUC (classification), MAE/RMSE (regression)

## Slide 11 — Returns Model — Training & Evaluation
- Preprocessing specific to returns (label extraction, time windowing)
- Balancing techniques (SMOTE, weighted loss) if rare returns
- Interpretability: SHAP or feature importance for returns drivers

## Slide 12 — Agents & How Models are Used
- `forecasting_agent`: calls demand model to produce forecasts
- `pricing_agent`: uses elasticity and forecast to set prices
- `inventory_agent`: sets reorder points using forecast and returns estimates
- `marketing_agent`: chooses spend to influence demand
- `returns_agent`: simulates returns events used to update inventory and revenue

## Slide 13 — Simulation Flow (Timestep)
- Forecast → Decide (pricing/marketing/reorder) → Realize sales sampling → Sample returns → Update inventory → Log metrics
- Feedback loops: realized sales update next-step features

## Slide 14 — Results & KPIs
- Revenue, Profit, Gross Margin
- Fill Rate, Stockouts, Days of Inventory
- Return Rate, Return Cost
- Forecast accuracy trends

## Slide 15 — Model-Centric Insights
- Which features mattered most for demand and returns
- How forecast error translates to KPI degradation
- Sensitivity analysis: pricing elasticity and inventory safety stock

## Slide 16 — Demo / How to run
- Generate data: `python generate_data.py`
- Train / view models: open Notebooks/*
- Run simulation: `python simulate.py`
- Build presentation (optional): `python scripts/build_presentation.py`

## Slide 17 — Next steps & Extensions
- Connect to real transactional data
- Replace heuristics with RL for pricing/inventory
- Online retraining and live A/B tests

## Slide 18 — Appendix (Key code snippets)
- Snippet: forecasting agent call
- Snippet: pricing decision logic
- Snippet: training snippet from notebook

## Slide 19 — References / Questions
- Repo path and contact


---

Notes:
- The presentation generator will convert headings into slides and bullets into content.
- Edit `presentation_outline.md` to tweak slide text before building the PPTX.
