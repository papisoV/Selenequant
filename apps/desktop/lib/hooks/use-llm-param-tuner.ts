"use client";

import { useCallback, useState } from "react";

// AI 推荐参数范围 hook
// 来源：旧代码 apps/desktop/lib/hooks/use-llm-param-tuner.ts（313 行，只学逻辑不搬视觉）
// 按 strategy 类别预定义参数范围，根据 MarketState 调整范围宽度

export interface MarketState {
  volatility: "low" | "medium" | "high";
  trendStrength: "weak" | "moderate" | "strong";
  recommendedStrategyTypes?: string[];
  paramAdjustmentSuggestion?: string;
}

export interface ParamRange {
  param: string;
  min: number;
  max: number;
  step: number;
  reason?: string;
}

export interface TemplateParams {
  templateId: string;
  templateName: string;
  category: string;
  paramRanges: ParamRange[];
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export interface UseLLMParamTunerOptions {
  maxParamCombinations?: number;
}

// AI 推荐参数范围：按 strategy 类别预定义
const AI_PARAM_RANGES: Record<string, Record<string, { min: number; max: number; default: number }>> = {
  trend: {
    sma_short: { min: 5, max: 30, default: 10 },
    sma_long: { min: 20, max: 100, default: 50 },
    ema_fast: { min: 5, max: 20, default: 12 },
    ema_slow: { min: 20, max: 50, default: 26 },
  },
  oscillator: {
    rsi_period: { min: 7, max: 21, default: 14 },
    rsi_oversold: { min: 20, max: 35, default: 30 },
    rsi_overbought: { min: 65, max: 80, default: 70 },
    stoch_k: { min: 5, max: 21, default: 14 },
    stoch_d: { min: 3, max: 7, default: 3 },
  },
  volatility: {
    bb_period: { min: 10, max: 30, default: 20 },
    bb_std: { min: 1.5, max: 3, default: 2 },
    atr_period: { min: 7, max: 21, default: 14 },
    atr_multiplier: { min: 1.5, max: 3, default: 2 },
  },
  volume: {
    obv_period: { min: 10, max: 30, default: 20 },
    mfi_period: { min: 10, max: 20, default: 14 },
    cmf_period: { min: 10, max: 30, default: 20 },
  },
  sentiment: {
    sentiment_threshold: { min: 0.3, max: 0.7, default: 0.5 },
    lookback_period: { min: 5, max: 20, default: 10 },
  },
  default: {
    period: { min: 5, max: 30, default: 14 },
    threshold: { min: 1, max: 5, default: 2 },
  },
};

/**
 * Generate parameter ranges for a template based on market state
 */
export function generateParamRanges(
  templateId: string,
  templateName: string,
  category: string,
  marketState: MarketState,
): TemplateParams {
  const baseRanges = AI_PARAM_RANGES[category] || AI_PARAM_RANGES.default;

  // Adjust range width based on volatility
  const volatilityMultiplier =
    marketState.volatility === "high" ? 1.5 : marketState.volatility === "low" ? 0.7 : 1.0;

  const paramRanges: ParamRange[] = Object.entries(baseRanges).map(([param, range]) => {
    const rangeWidth = range.max - range.min;
    const adjustedWidth = rangeWidth * volatilityMultiplier;

    return {
      param,
      min: range.default - adjustedWidth / 2,
      max: range.default + adjustedWidth / 2,
      step: (range.max - range.min) / 5,
      reason: `Based on ${marketState.volatility} volatility and ${marketState.trendStrength} trend`,
    };
  });

  return {
    templateId,
    templateName,
    category,
    paramRanges,
  };
}

/**
 * Validate a parameter range
 */
export function validateParamRange(range: ParamRange): ValidationResult {
  const errors: string[] = [];

  if (range.min >= range.max) {
    errors.push(`min (${range.min}) must be less than max (${range.max})`);
  }
  if (range.step <= 0) {
    errors.push(`step (${range.step}) must be greater than 0`);
  }

  const rangeWidth = range.max - range.min;
  if (rangeWidth < 1) {
    errors.push(`Range width (${rangeWidth}) is too narrow (minimum 1)`);
  }
  if (rangeWidth > 1000) {
    errors.push(`Range width (${rangeWidth}) is too wide (maximum 1000)`);
  }

  if (range.min < 0) {
    errors.push(`min (${range.min}) cannot be negative`);
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * useLLMParamTuner — AI 推荐参数范围 hook
 * 用法: const { paramRanges, generateForTemplate } = useLLMParamTuner(templateId, marketState)
 * 也可不带参数用 generateForTemplates 批量生成
 */
export function useLLMParamTuner(
  templateId?: string,
  marketState?: MarketState,
  options: UseLLMParamTunerOptions = {},
) {
  const { maxParamCombinations = 50 } = options;

  const [isGenerating, setIsGenerating] = useState(false);
  const [templateParams, setTemplateParams] = useState<TemplateParams[]>([]);
  const [errors, setErrors] = useState<string[]>([]);

  // 单模板参数范围（templateId + marketState 提供时计算）
  const paramRanges: ParamRange[] = (() => {
    if (!templateId || !marketState) return [];
    const tp = generateParamRanges(templateId, templateId, "default", marketState);
    return tp.paramRanges;
  })();

  const generateForTemplates = useCallback(
    (
      templates: Array<{ id: string; name: string; category: string }>,
      ms: MarketState,
    ): TemplateParams[] => {
      setIsGenerating(true);
      setErrors([]);

      try {
        const params = templates.map((template) =>
          generateParamRanges(template.id, template.name, template.category, ms),
        );

        const totalCombinations = params.reduce((acc, p) => acc + p.paramRanges.length, 0);

        if (totalCombinations > maxParamCombinations) {
          const warning = `Total combinations (${totalCombinations}) exceeds limit (${maxParamCombinations}). Some parameters will be truncated.`;
          setErrors([warning]);
        }

        setTemplateParams(params);
        return params;
      } finally {
        setIsGenerating(false);
      }
    },
    [maxParamCombinations],
  );

  const generateForTemplate = useCallback(
    (
      template: { id: string; name: string; category: string },
      ms: MarketState,
    ): TemplateParams => {
      return generateParamRanges(template.id, template.name, template.category, ms);
    },
    [],
  );

  const validateTemplateParams = useCallback((tp: TemplateParams): ValidationResult => {
    const allErrors: string[] = [];

    for (const range of tp.paramRanges) {
      const result = validateParamRange(range);
      if (!result.valid) {
        allErrors.push(...result.errors.map((e) => `${tp.templateName}.${range.param}: ${e}`));
      }
    }

    return {
      valid: allErrors.length === 0,
      errors: allErrors,
    };
  }, []);

  const validateAll = useCallback((): ValidationResult => {
    const allErrors: string[] = [];

    for (const tp of templateParams) {
      const result = validateTemplateParams(tp);
      allErrors.push(...result.errors);
    }

    return {
      valid: allErrors.length === 0,
      errors: allErrors,
    };
  }, [templateParams, validateTemplateParams]);

  const generateSingleParams = useCallback((tp: TemplateParams): Record<string, number> => {
    const params: Record<string, number> = {};

    for (const range of tp.paramRanges) {
      const steps = Math.floor((range.max - range.min) / range.step);
      const midStep = Math.floor(steps / 2);
      params[range.param] = Math.round((range.min + midStep * range.step) * 100) / 100;
    }

    return params;
  }, []);

  const generateParamGrid = useCallback(
    (tp: TemplateParams, maxCombinations: number = 10): Record<string, number>[] => {
      if (tp.paramRanges.length === 0) return [{}];

      const results: Record<string, number>[] = [{}];

      for (const range of tp.paramRanges) {
        const newResults: Record<string, number>[] = [];

        for (const base of results) {
          for (let value = range.min; value <= range.max; value += range.step) {
            if (newResults.length >= maxCombinations) break;
            newResults.push({
              ...base,
              [range.param]: Math.round(value * 100) / 100,
            });
          }
        }

        results.length = 0;
        results.push(...newResults.slice(0, maxCombinations));

        if (results.length >= maxCombinations) break;
      }

      return results;
    },
    [],
  );

  const reset = useCallback(() => {
    setTemplateParams([]);
    setErrors([]);
    setIsGenerating(false);
  }, []);

  return {
    // 单模板参数范围（templateId + marketState 提供时）
    paramRanges,
    // 批量状态
    isGenerating,
    templateParams,
    errors,
    // Actions
    generateForTemplate,
    generateForTemplates,
    validateTemplateParams,
    validateAll,
    validateParamRange,
    generateSingleParams,
    generateParamGrid,
    generateParamRanges,
    reset,
  };
}
