"""Factor computation modules - both torch and pandas implementations.

Torch-native modules (meme_indicators, feature_engineer) are not imported at
package level to avoid triggering torch on systems where it is unavailable.
Import directly when needed:
    from selene_space_alpha.factors.meme_indicators import MemeIndicators
    from selene_space_alpha.factors.feature_engineer import FeatureEngineer
"""

__all__ = ["FeatureEngineer", "AdvancedFactorEngineer", "MemeIndicators"]
