#!/usr/bin/env Rscript

# R backend for statistics_utils.py.
# Input is a single long-format CSV prepared by Python. The script splits the
# data by session and trial type, then runs:
#   - balanced/no-missing data: repeated-measures 2-way ANOVA via afex
#   - missing cells: mixed-effects model via lmerTest/lme4

# Use sum-to-zero contrasts so Type III tests match common factorial ANOVA
# conventions used by afex/lmerTest/SPSS/Prism-style workflows.
options(contrasts = c("contr.sum", "contr.poly"))


# =============================================================================
# User-tunable model settings
# =============================================================================
#
# Most users should configure statistics from runner.py. The constants below are
# lower-level modeling choices that a stats-savvy user may wish to adjust.

# Mixed-model optimizer settings. Increase LMER_MAXFUN if lmer reports that the
# optimizer hit its iteration/function-evaluation limit.
LMER_OPTIMIZER <- "bobyqa"
LMER_MAXFUN <- 100000

# Random-effect variance/singularity tolerances. If a random-effect variance is
# at or below RANDOM_VARIANCE_TOL, or lme4 flags the model as singular using
# SINGULAR_TOL, the script fits a simpler random-effects structure.
RANDOM_VARIANCE_TOL <- 1e-8
SINGULAR_TOL <- 1e-4

# Denominator degrees-of-freedom method for mixed-model ANOVA tables. Common
# alternatives are "Satterthwaite" and "Kenward-Roger"; Kenward-Roger may need
# extra packages and can be slower.
MIXED_DDF_METHOD <- "Satterthwaite"

# Floor used when GLM-derived starting values are zero/non-finite. This keeps
# lmer from starting exactly at a zero random-effect variance.
GLM_THETA_FLOOR <- 0.1


# =============================================================================
# Command-line arguments from Python
# =============================================================================

args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(flag, default = NULL) {
  idx <- match(flag, args)
  if (is.na(idx) || idx >= length(args)) {
    return(default)
  }
  args[[idx + 1]]
}

input_path <- get_arg("--input")
results_path <- get_arg("--results")
log_path <- get_arg("--log")
use_gg <- tolower(get_arg("--use_gg", "true")) %in% c("1", "true", "t", "yes", "y")

if (is.null(input_path) || is.null(results_path) || is.null(log_path)) {
  stop("Usage: Rscript statistics_2way_anova.R --input in.csv --results out.csv --log log.csv --use_gg true")
}


# =============================================================================
# Small shared helpers
# =============================================================================

# Packages are loaded lazily so users only need the packages required by the
# model path their data actually take. Missing packages produce a clear message
# that is captured by the Python wrapper in the model log.
ensure_pkg <- function(pkg) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    stop(sprintf(
      "Missing required R package '%s'. Install it in R with install.packages('%s').",
      pkg, pkg
    ), call. = FALSE)
  }
}

# These lists accumulate rows for the final CSV outputs. The script writes one
# statistics table and one model log per Python wrapper call.
result_rows <- list()
log_rows <- list()

# Add one row to the final ANOVA/statistics results table.
add_result <- function(scope, scope_cohort, measure, session, trial_type, effect,
                       method, correction, df1 = NA_real_, df2 = NA_real_,
                       statistic = NA_real_, p_value = NA_real_,
                       p_corrected = NA_real_, epsilon_gg = NA_real_,
                       ges = NA_real_, n_subjects = NA_integer_,
                       n_rows = NA_integer_, n_timepoints = NA_integer_,
                       n_treatments = NA_integer_, missing_cells = NA_integer_,
                       model_formula = "", model_note = "", source_columns = "") {
  result_rows[[length(result_rows) + 1]] <<- data.frame(
    analysis_scope = scope,
    analysis_cohort_id = scope_cohort,
    measure = measure,
    session = session,
    trial_type = trial_type,
    effect = effect,
    method = method,
    correction = correction,
    df1 = df1,
    df2 = df2,
    statistic = statistic,
    p_value = p_value,
    p_corrected = p_corrected,
    epsilon_gg = epsilon_gg,
    generalized_eta_squared = ges,
    n_subjects = n_subjects,
    n_rows = n_rows,
    n_timepoints = n_timepoints,
    n_treatments = n_treatments,
    missing_cells = missing_cells,
    model_formula = model_formula,
    model_note = model_note,
    source_columns = source_columns,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
}

# Add one row to the model log. This records skipped analyses, model choices,
# fallbacks, warnings, and R/package errors in a user-readable table.
add_log <- function(scope, scope_cohort, measure, session, trial_type,
                    method, status, model_formula, n_subjects, n_rows,
                    n_timepoints, n_treatments, missing_cells, message) {
  log_rows[[length(log_rows) + 1]] <<- data.frame(
    analysis_scope = scope,
    analysis_cohort_id = scope_cohort,
    measure = measure,
    session = session,
    trial_type = trial_type,
    method = method,
    status = status,
    model_formula = model_formula,
    n_subjects = n_subjects,
    n_rows = n_rows,
    n_timepoints = n_timepoints,
    n_treatments = n_treatments,
    missing_cells = missing_cells,
    message = message,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
}

# Different R packages use different column names for the same statistics. This
# helper finds the first matching numeric column from a list of likely names.
num_col <- function(tab, candidates) {
  for (candidate in candidates) {
    if (candidate %in% names(tab)) {
      return(suppressWarnings(as.numeric(tab[[candidate]])))
    }
  }
  rep(NA_real_, nrow(tab))
}

# Kept for future package-output variants that need text columns.
text_col <- function(tab, candidates) {
  for (candidate in candidates) {
    if (candidate %in% names(tab)) {
      return(as.character(tab[[candidate]]))
    }
  }
  rep("", nrow(tab))
}

# Keep the output focused on the three requested tests: time, treatment, and
# their interaction.
wanted_effect <- function(effect) {
  effect %in% c("time", "treatment_group", "time:treatment_group")
}

# Some packages may report the interaction in the opposite order.
normalize_effect <- function(effect) {
  if (effect == "treatment_group:time") {
    return("time:treatment_group")
  }
  effect
}

# Write final CSVs even if no model succeeded, so Python and users always have
# an inspectable artifact.
write_outputs <- function() {
  if (length(result_rows) == 0) {
    results <- data.frame(
      analysis_scope = character(), analysis_cohort_id = character(),
      measure = character(), session = character(), trial_type = character(),
      effect = character(), method = character(), correction = character(),
      df1 = numeric(), df2 = numeric(), statistic = numeric(),
      p_value = numeric(), p_corrected = numeric(), epsilon_gg = numeric(),
      generalized_eta_squared = numeric(), n_subjects = integer(),
      n_rows = integer(), n_timepoints = integer(), n_treatments = integer(),
      missing_cells = integer(), model_formula = character(),
      model_note = character(), source_columns = character(),
      check.names = FALSE
    )
  } else {
    results <- do.call(rbind, result_rows)
  }

  if (length(log_rows) == 0) {
    logs <- data.frame(
      analysis_scope = character(), analysis_cohort_id = character(),
      measure = character(), session = character(), trial_type = character(),
      method = character(), status = character(), model_formula = character(),
      n_subjects = integer(), n_rows = integer(), n_timepoints = integer(),
      n_treatments = integer(), missing_cells = integer(), message = character(),
      check.names = FALSE
    )
  } else {
    logs <- do.call(rbind, log_rows)
  }

  write.csv(results, results_path, row.names = FALSE)
  write.csv(logs, log_path, row.names = FALSE)
}

# Capture warnings as strings instead of printing them to the console only. The
# warning text is added to model_note/model_log so it stays with the results.
with_warnings <- function(expr) {
  warnings <- character()
  value <- withCallingHandlers(
    expr,
    warning = function(w) {
      warnings <<- c(warnings, conditionMessage(w))
      invokeRestart("muffleWarning")
    }
  )
  list(value = value, warnings = warnings)
}


# =============================================================================
# Initial values for mixed models
# =============================================================================

# lmer can be sensitive to poor starting values, especially with sparse/missing
# repeated-measures data. This function fits a simple Gaussian GLM first, then
# uses the residual spread to create reasonable starting values for random
# intercepts and random time slopes.
estimate_theta_from_glm <- function(d, slope = TRUE) {
  glm_fit <- stats::glm(value ~ time * treatment_group, data = d, family = gaussian())
  res <- stats::residuals(glm_fit)
  sigma <- sqrt(summary(glm_fit)$dispersion)
  if (!is.finite(sigma) || sigma <= 0) {
    sigma <- stats::sd(res, na.rm = TRUE)
  }
  if (!is.finite(sigma) || sigma <= 0) {
    sigma <- 1
  }

  intercept_means <- stats::aggregate(
    res, by = list(subject_id = d$subject_id), FUN = mean, na.rm = TRUE)
  intercept_theta <- stats::sd(intercept_means$x, na.rm = TRUE) / sigma
  if (!is.finite(intercept_theta) || intercept_theta <= 0) {
    intercept_theta <- GLM_THETA_FLOOR
  }

  if (!slope) {
    return(c(intercept_theta))
  }

  slopes <- stats::aggregate(
    seq_along(res), by = list(subject_id = d$subject_id),
    FUN = function(ix) {
      z <- data.frame(res = res[ix], time_num_scaled = d$time_num_scaled[ix])
      z <- z[stats::complete.cases(z), , drop = FALSE]
      if (nrow(z) < 2 || length(unique(z$time_num_scaled)) < 2) {
        return(NA_real_)
      }
      stats::coef(stats::lm(res ~ time_num_scaled, data = z))[["time_num_scaled"]]
    }
  )
  slope_theta <- stats::sd(slopes$x, na.rm = TRUE) / sigma
  if (!is.finite(slope_theta) || slope_theta <= 0) {
    slope_theta <- GLM_THETA_FLOOR
  }
  c(intercept_theta, 0, slope_theta)
}


# =============================================================================
# Mixed-model fitting and fallback logic
# =============================================================================

# Fit lmer with GLM-derived starting values first. If those starts are rejected,
# retry with lmer's defaults before giving up.
fit_lmer <- function(formula, d, theta) {
  ensure_pkg("lmerTest")
  ensure_pkg("lme4")
  control <- lme4::lmerControl(
    optimizer = LMER_OPTIMIZER,
    optCtrl = list(maxfun = LMER_MAXFUN)
  )

  started <- TRUE
  fit_try <- try(with_warnings(
    lmerTest::lmer(formula, data = d, REML = FALSE,
                   start = list(theta = theta), control = control)
  ), silent = TRUE)

  if (inherits(fit_try, "try-error")) {
    started <- FALSE
    fit_try <- try(with_warnings(
      lmerTest::lmer(formula, data = d, REML = FALSE, control = control)
    ), silent = TRUE)
  }

  if (inherits(fit_try, "try-error")) {
    stop(as.character(fit_try), call. = FALSE)
  }
  list(fit = fit_try$value, warnings = fit_try$warnings, started = started)
}

# Decide whether a fitted mixed model has a zero/near-zero random-effect
# variance or singular random-effects covariance. If so, we simplify the model.
variance_problem <- function(fit, slope = TRUE, tol = RANDOM_VARIANCE_TOL) {
  vc <- as.data.frame(lme4::VarCorr(fit))
  variances <- vc$vcov[is.na(vc$var2)]
  zero_var <- any(!is.finite(variances) | variances <= tol)
  slope_zero <- FALSE
  if (slope && "time_num_scaled" %in% vc$var1) {
    slope_var <- vc$vcov[vc$var1 == "time_num_scaled" & is.na(vc$var2)]
    slope_zero <- length(slope_var) == 0 || any(!is.finite(slope_var) | slope_var <= tol)
  }
  lme4::isSingular(fit, tol = SINGULAR_TOL) || zero_var || slope_zero
}

# Convert package-specific ANOVA tables into the pipeline's shared CSV schema.
append_anova_table <- function(tab, scope, scope_cohort, measure, session, trial_type,
                               method, correction, n_subjects, n_rows,
                               n_timepoints, n_treatments, missing_cells,
                               formula_text, note) {
  effects <- rownames(tab)
  if ("Effect" %in% names(tab)) {
    effects <- as.character(tab$Effect)
  }
  if ("term" %in% names(tab)) {
    effects <- as.character(tab$term)
  }

  df1 <- num_col(tab, c("num Df", "num.Df", "NumDF", "Df", "Df1"))
  df2 <- num_col(tab, c("den Df", "den.Df", "DenDF", "Res.Df", "Df2"))
  stat <- num_col(tab, c("F", "F value", "F.value", "F.value."))
  p <- num_col(tab, c("Pr(>F)", "Pr..F.", "p.value", "p"))
  eps <- num_col(tab, c("GGe", "GG eps", "GG.eps", "epsilon_GG"))
  ges <- num_col(tab, c("ges", "generalized_eta_squared"))

  for (i in seq_along(effects)) {
    effect <- normalize_effect(effects[[i]])
    if (!wanted_effect(effect)) {
      next
    }
    add_result(
      scope, scope_cohort, measure, session, trial_type, effect,
      method, correction, df1[[i]], df2[[i]], stat[[i]], p[[i]], p[[i]],
      eps[[i]], ges[[i]], n_subjects, n_rows, n_timepoints, n_treatments,
      missing_cells, formula_text, note, paste(names(tab), collapse = " | ")
    )
  }
}


# =============================================================================
# Balanced-data path: repeated-measures 2-way ANOVA
# =============================================================================

# Used when every subject has every trial_index/time cell for this session and
# trial type. Greenhouse-Geisser correction is controlled by runner.py and only
# applies to this RM ANOVA path.
run_rm_anova <- function(d, meta) {
  ensure_pkg("afex")
  correction <- if (use_gg) "GG" else "none"
  formula_text <- "value ~ time * treatment_group + Error(subject_id/time)"
  fit_info <- with_warnings(
    afex::aov_ez(
      id = "subject_id",
      dv = "value",
      data = d,
      within = "time",
      between = "treatment_group",
      type = 3,
      factorize = FALSE,
      anova_table = list(correction = correction, es = "ges")
    )
  )
  tab <- as.data.frame(fit_info$value$anova_table)
  note <- paste(c(
    "balanced complete cells",
    if (use_gg) "Greenhouse-Geisser correction requested" else "no sphericity correction requested",
    fit_info$warnings
  ), collapse = "; ")
  append_anova_table(
    tab, meta$scope, meta$scope_cohort, meta$measure, meta$session,
    meta$trial_type, "rm_anova_afex", correction, meta$n_subjects,
    meta$n_rows, meta$n_timepoints, meta$n_treatments, meta$missing_cells,
    formula_text, note
  )
  add_log(
    meta$scope, meta$scope_cohort, meta$measure, meta$session, meta$trial_type,
    "rm_anova_afex", "ok", formula_text, meta$n_subjects, meta$n_rows,
    meta$n_timepoints, meta$n_treatments, meta$missing_cells, note
  )
}


# =============================================================================
# Missing-data path: mixed model, then simpler fallbacks if needed
# =============================================================================

# Final fallback if all random effects are unusable. This keeps the output
# honest: model_note explicitly says random terms were removed.
run_lm_fallback <- function(d, meta, previous_notes) {
  ensure_pkg("car")
  formula_text <- "value ~ time * treatment_group"
  fit_info <- with_warnings(stats::lm(value ~ time * treatment_group, data = d))
  tab <- as.data.frame(car::Anova(fit_info$value, type = 3))
  note <- paste(c(previous_notes, "all random-effect terms removed", fit_info$warnings),
                collapse = "; ")
  append_anova_table(
    tab, meta$scope, meta$scope_cohort, meta$measure, meta$session,
    meta$trial_type, "lm_no_random_effects", "not_applicable",
    meta$n_subjects, meta$n_rows, meta$n_timepoints, meta$n_treatments,
    meta$missing_cells, formula_text, note
  )
  add_log(
    meta$scope, meta$scope_cohort, meta$measure, meta$session, meta$trial_type,
    "lm_no_random_effects", "fallback", formula_text, meta$n_subjects,
    meta$n_rows, meta$n_timepoints, meta$n_treatments, meta$missing_cells, note
  )
}

# Missing cells break the balanced RM ANOVA assumptions, so use mixed models.
# The script tries:
#   1. random intercept + random time slope by subject
#   2. random intercept only by subject
#   3. fixed-effects LM if random-effect variances are zero/singular
run_mixed_model <- function(d, meta) {
  notes <- c("missing cells detected; GG correction not applicable to mixed model")
  d$time_num_scaled <- as.numeric(scale(d$time_numeric))
  if (all(!is.finite(d$time_num_scaled))) {
    d$time_num_scaled <- d$time_numeric
  }
  d$time_num_scaled[!is.finite(d$time_num_scaled)] <- 0

  formula_slope <- value ~ time * treatment_group + (1 + time_num_scaled | subject_id)
  theta_slope <- estimate_theta_from_glm(d, slope = TRUE)
  fit1 <- try(fit_lmer(formula_slope, d, theta_slope), silent = TRUE)
  if (!inherits(fit1, "try-error")) {
    note1 <- c(notes, "random-effect start values estimated from Gaussian GLM residuals")
    if (fit1$started) {
      note1 <- c(note1, paste("theta_start =", paste(round(theta_slope, 6), collapse = ",")))
    } else {
      note1 <- c(note1, "GLM-derived start rejected; refit without explicit start")
    }
    note1 <- c(note1, fit1$warnings)
    if (!variance_problem(fit1$fit, slope = TRUE)) {
      tab <- as.data.frame(stats::anova(fit1$fit, type = 3, ddf = MIXED_DDF_METHOD))
      append_anova_table(
        tab, meta$scope, meta$scope_cohort, meta$measure, meta$session,
        meta$trial_type, "mixed_lmer_random_intercept_slope",
        "not_applicable_mixed_model", meta$n_subjects, meta$n_rows,
        meta$n_timepoints, meta$n_treatments, meta$missing_cells,
        deparse(formula_slope), paste(note1, collapse = "; ")
      )
      add_log(
        meta$scope, meta$scope_cohort, meta$measure, meta$session, meta$trial_type,
        "mixed_lmer_random_intercept_slope", "ok", deparse(formula_slope),
        meta$n_subjects, meta$n_rows, meta$n_timepoints, meta$n_treatments,
        meta$missing_cells, paste(note1, collapse = "; ")
      )
      return(invisible(NULL))
    }
    notes <- c(note1, "random slope/intercept covariance singular or variance <= 0; removing random slope")
  } else {
    notes <- c(notes, paste("random slope model failed:", as.character(fit1)))
  }

  formula_intercept <- value ~ time * treatment_group + (1 | subject_id)
  theta_intercept <- estimate_theta_from_glm(d, slope = FALSE)
  fit2 <- try(fit_lmer(formula_intercept, d, theta_intercept), silent = TRUE)
  if (!inherits(fit2, "try-error")) {
    note2 <- c(notes, "random-intercept start values estimated from Gaussian GLM residuals")
    if (fit2$started) {
      note2 <- c(note2, paste("theta_start =", paste(round(theta_intercept, 6), collapse = ",")))
    } else {
      note2 <- c(note2, "GLM-derived start rejected; refit without explicit start")
    }
    note2 <- c(note2, fit2$warnings)
    if (!variance_problem(fit2$fit, slope = FALSE)) {
      tab <- as.data.frame(stats::anova(fit2$fit, type = 3, ddf = MIXED_DDF_METHOD))
      append_anova_table(
        tab, meta$scope, meta$scope_cohort, meta$measure, meta$session,
        meta$trial_type, "mixed_lmer_random_intercept",
        "not_applicable_mixed_model", meta$n_subjects, meta$n_rows,
        meta$n_timepoints, meta$n_treatments, meta$missing_cells,
        deparse(formula_intercept), paste(note2, collapse = "; ")
      )
      add_log(
        meta$scope, meta$scope_cohort, meta$measure, meta$session, meta$trial_type,
        "mixed_lmer_random_intercept", "fallback", deparse(formula_intercept),
        meta$n_subjects, meta$n_rows, meta$n_timepoints, meta$n_treatments,
        meta$missing_cells, paste(note2, collapse = "; ")
      )
      return(invisible(NULL))
    }
    notes <- c(note2, "random intercept variance <= 0 or singular; removing all random effects")
  } else {
    notes <- c(notes, paste("random intercept model failed:", as.character(fit2)))
  }

  run_lm_fallback(d, meta, notes)
}


# =============================================================================
# Data preparation for one analysis block
# =============================================================================

# One block = one measure x one session x one trial type x one analysis scope.
# Duplicate subject/time rows are averaged here so each subject contributes one
# value per timepoint.
prepare_block <- function(d) {
  d <- d[stats::complete.cases(d[, c("subject_id", "treatment_group", "time", "time_numeric", "value")]), ]
  d$value <- as.numeric(d$value)
  d$time_numeric <- as.numeric(d$time_numeric)

  d <- stats::aggregate(
    value ~ subject_id + treatment_group + time + time_numeric,
    data = d,
    FUN = function(x) mean(x, na.rm = TRUE)
  )

  time_lookup <- stats::aggregate(time_numeric ~ time, data = d, FUN = min, na.rm = TRUE)
  time_levels <- as.character(time_lookup$time[order(time_lookup$time_numeric)])
  treatment_levels <- unique(as.character(d$treatment_group))

  d$time <- factor(as.character(d$time), levels = time_levels)
  d$treatment_group <- factor(as.character(d$treatment_group), levels = treatment_levels)
  d
}

# Check whether a block is analyzable, choose RM ANOVA vs mixed model, and log
# clear reasons for skipped/failed analyses.
process_block <- function(block) {
  scope <- unique(block$analysis_scope)[[1]]
  scope_cohort <- unique(block$analysis_cohort_id)[[1]]
  measure <- unique(block$measure)[[1]]
  session <- unique(block$session)[[1]]
  trial_type <- unique(block$trial_type)[[1]]

  d <- prepare_block(block)
  n_subjects <- length(unique(d$subject_id))
  n_timepoints <- length(unique(d$time))
  n_treatments <- length(unique(d$treatment_group))
  n_rows <- nrow(d)

  grid <- expand.grid(
    subject_id = unique(d$subject_id),
    time = levels(d$time),
    stringsAsFactors = FALSE
  )
  present <- unique(paste(d$subject_id, as.character(d$time), sep = "\r"))
  missing_cells <- nrow(grid) - sum(paste(grid$subject_id, grid$time, sep = "\r") %in% present)

  meta <- list(
    scope = scope,
    scope_cohort = scope_cohort,
    measure = measure,
    session = session,
    trial_type = trial_type,
    n_subjects = n_subjects,
    n_rows = n_rows,
    n_timepoints = n_timepoints,
    n_treatments = n_treatments,
    missing_cells = missing_cells
  )

  if (n_rows == 0 || n_subjects < 2 || n_timepoints < 2 || n_treatments < 2) {
    add_log(
      scope, scope_cohort, measure, session, trial_type, "not_run",
      "skipped", "", n_subjects, n_rows, n_timepoints, n_treatments,
      missing_cells, "Insufficient subjects, timepoints, or treatment groups."
    )
    return(invisible(NULL))
  }

  subject_treatments <- stats::aggregate(
    treatment_group ~ subject_id, data = d,
    FUN = function(x) length(unique(as.character(x)))
  )
  if (any(subject_treatments$treatment_group > 1)) {
    add_log(
      scope, scope_cohort, measure, session, trial_type, "not_run",
      "skipped", "", n_subjects, n_rows, n_timepoints, n_treatments,
      missing_cells, "At least one subject appears in more than one treatment group."
    )
    return(invisible(NULL))
  }

  tryCatch({
    if (missing_cells == 0) {
      run_rm_anova(d, meta)
    } else {
      run_mixed_model(d, meta)
    }
  }, error = function(e) {
    add_log(
      scope, scope_cohort, measure, session, trial_type, "model_failed",
      "error", "", n_subjects, n_rows, n_timepoints, n_treatments,
      missing_cells, conditionMessage(e)
    )
  })
}


# =============================================================================
# Main script body
# =============================================================================

input <- read.csv(input_path, stringsAsFactors = FALSE, check.names = FALSE)
required <- c(
  "analysis_scope", "analysis_cohort_id", "measure", "subject_id",
  "treatment_group", "session", "trial_type", "time", "time_numeric", "value"
)
missing_required <- setdiff(required, names(input))
if (length(missing_required) > 0) {
  stop(paste("Input CSV missing required columns:", paste(missing_required, collapse = ", ")))
}

keys <- unique(input[, c("analysis_scope", "analysis_cohort_id", "measure", "session", "trial_type")])

# Run each model independently. This is why one failed session/trial type does
# not stop the rest of the statistics output from being written.
for (i in seq_len(nrow(keys))) {
  key <- keys[i, , drop = FALSE]
  block <- input[
    input$analysis_scope == key$analysis_scope &
      input$analysis_cohort_id == key$analysis_cohort_id &
      input$measure == key$measure &
      input$session == key$session &
      input$trial_type == key$trial_type,
    ,
    drop = FALSE
  ]
  process_block(block)
}

write_outputs()
