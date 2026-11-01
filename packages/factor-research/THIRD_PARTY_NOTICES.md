# Third-Party Notices — factor-research

Portions of this package are ported from third-party open-source projects.
Notices are provided as required by each project's license.

## AlphaPurify (MIT)

The preprocessing toolbox under
`selene_space_factor_research/preprocessing/` (winsorize / neutralize /
standardize), the weight-drift backtest modules, and the IC diagnostic
panel under `selene_space_factor_research/mining/` are ported from
**AlphaPurify**, version 1.0.1.

- Project: AlphaPurify
- License: MIT
- Copyright: (c) 2026 Elias Wu

Ported files carry the marker `# Ported from AlphaPurify (MIT)` at the top.
The Selene port replaces AlphaPurify's `polars` implementation with a
`pandas`/`numpy`/`scipy`/`scikit-learn` rewrite; behaviour is intended to be
functionally identical (golden-fixture parity tests live under
`packages/alpha-core/tests/fixtures/`).

MIT License (applies to the ported code listed above):

```
MIT License

Copyright (c) 2026 Elias Wu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
