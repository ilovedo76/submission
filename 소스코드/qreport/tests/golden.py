"""Golden numbers from the planning document (section 5). Rates are fractions."""
PBAR = 0.00035
UCL_2512 = 0.00070
# model -> (defects, production, 1MIS rate, UCL)
MODEL_2512 = {"양문형": (10, 4155, 0.00241, 0.00144)}
BETA = {"PRIOR": (1.06, 0.96, 1.16), "S2": (0.80, 0.68, 0.95), "S1": (0.41, 0.23, 0.74)}
BETA_MODEL = {"2Door": (0.48, 0.30, 0.74), "양문형": (0.61, 0.45, 0.83),
              "4Door": (0.67, 0.53, 0.85), "김치": (0.88, 0.66, 1.18)}
KIMCHI_6MIS = (0.00167, 0.00315)   # (prior_12, recent_12) at base 2025-12
MARKET = (0.00122, 0.00191)        # (2024, 2025)
FORECAST = (101, 114)
MIS3_2508_2510 = (0.00110, 0.00146)  # (min, max) of 3MIS rates, prod months 2025-08..10
