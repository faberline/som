//! The production SOM decoder. Python only trains and exports these weights.

use candle_core::{DType, Device, IndexOp, Result as CResult, Tensor};
use candle_nn::{Embedding, LayerNorm, Linear, Module, VarBuilder};
use serde::Deserialize;
use serde_json::Value;
use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};
use tokenizers::Tokenizer;

use crate::{LOGIC_STEPS, LOGIC_STEPS_V2, LOGIC_STEPS_V3, LOGIC_STEPS_V4, SomError};

const ALL_OPERATIONS: [&str; 8] = [
    "api.create",
    "api.get",
    "api.list",
    "api.list.query",
    "api.list.boolean",
    "api.list.boolean.date",
    "api.patch",
    "api.delete",
];

#[derive(Debug, Clone, Deserialize)]
pub struct ModelConfig {
    pub model_type: String,
    pub n_ctx: usize,
    pub n_embd: usize,
    pub n_head: usize,
    pub n_layer: usize,
    pub n_positions: usize,
    pub layer_norm_epsilon: f64,
    pub vocab_size: usize,
    pub max_new_tokens: usize,
    #[serde(default = "default_output_mode")]
    pub output_mode: String,
    #[serde(default)]
    pub training_profile: String,
    #[serde(default)]
    pub flow_decode_policy: String,
    #[serde(default)]
    pub flow_input_policy: String,
}

fn default_output_mode() -> String {
    "full_dsl".to_owned()
}

struct Layer {
    ln_1: LayerNorm,
    ln_2: LayerNorm,
    qkv: Linear,
    attn_out: Linear,
    fc: Linear,
    mlp_out: Linear,
}

impl Layer {
    fn load(vb: VarBuilder, cfg: &ModelConfig) -> CResult<Self> {
        let d = cfg.n_embd;
        Ok(Self {
            ln_1: candle_nn::layer_norm(d, cfg.layer_norm_epsilon, vb.pp("ln_1"))?,
            ln_2: candle_nn::layer_norm(d, cfg.layer_norm_epsilon, vb.pp("ln_2"))?,
            qkv: candle_nn::linear(d, 3 * d, vb.pp("attn.c_attn"))?,
            attn_out: candle_nn::linear(d, d, vb.pp("attn.c_proj"))?,
            fc: candle_nn::linear(d, 4 * d, vb.pp("mlp.c_fc"))?,
            mlp_out: candle_nn::linear(4 * d, d, vb.pp("mlp.c_proj"))?,
        })
    }

    fn forward(
        &self,
        x: &Tensor,
        heads: usize,
        mask: &Tensor,
        cache: &mut Option<(Tensor, Tensor)>,
    ) -> CResult<Tensor> {
        let (batch, seq, dim) = x.dims3()?;
        let norm = metal_layer_norm(&self.ln_1, x)?;
        let qkv = self.qkv.forward(&norm)?;
        let q = qkv
            .narrow(2, 0, dim)?
            .reshape((batch, seq, heads, dim / heads))?
            .transpose(1, 2)?
            .contiguous()?;
        let new_k = qkv
            .narrow(2, dim, dim)?
            .reshape((batch, seq, heads, dim / heads))?
            .transpose(1, 2)?
            .contiguous()?;
        let new_v = qkv
            .narrow(2, 2 * dim, dim)?
            .reshape((batch, seq, heads, dim / heads))?
            .transpose(1, 2)?
            .contiguous()?;
        let (k, v) = match cache.take() {
            Some((old_k, old_v)) => (
                Tensor::cat(&[&old_k, &new_k], 2)?.contiguous()?,
                Tensor::cat(&[&old_v, &new_v], 2)?.contiguous()?,
            ),
            None => (new_k, new_v),
        };
        *cache = Some((k.clone(), v.clone()));
        let scores = q
            .matmul(&k.transpose(2, 3)?.contiguous()?)?
            .affine(1.0 / ((dim / heads) as f64).sqrt(), 0.0)?;
        let scores = scores.broadcast_add(mask)?;
        let probs = candle_nn::ops::softmax(&scores, 3)?;
        let context = probs
            .contiguous()?
            .matmul(&v)?
            .transpose(1, 2)?
            .contiguous()?
            .reshape((batch, seq, dim))?;
        let attn = self.attn_out.forward(&context)?;
        let h = (x + attn)?;
        let mlp = self.mlp_out.forward(
            &self
                .fc
                .forward(&metal_layer_norm(&self.ln_2, &h)?)?
                .gelu()?,
        )?;
        &h + mlp
    }
}

pub struct Decoder {
    pub config: ModelConfig,
    tokenizer: Tokenizer,
    device: Device,
    wte: Embedding,
    wpe: Embedding,
    layers: Vec<Layer>,
    ln_f: LayerNorm,
    template: Option<Value>,
    legacy: Option<Box<Decoder>>,
    catalog: Option<Value>,
}

impl Decoder {
    pub fn warmup(&self) -> Result<(), SomError> {
        if let Some(legacy) = &self.legacy { legacy.warmup()?; }
        // Warm Metal kernels without creating a partial public prompt.
        // Accuracy is checked with complete requirements in the evaluation gate.
        for length in [16usize, 64, 128] {
            if length < self.config.n_ctx {
                let mut cache = vec![None; self.layers.len()];
                self.logits(&vec![0; length], 0, &mut cache)
                    .map_err(model_error)?.to_vec1::<f32>().map_err(model_error)?;
            }
        }
        Ok(())
    }

    pub fn load(dir: &Path) -> Result<Self, SomError> {
        let config_json: Value = serde_json::from_slice(
            &fs::read(dir.join("config.json"))
                .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?,
        )
        .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?;
        let cfg: ModelConfig = serde_json::from_value(config_json.clone())
            .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?;
        if cfg.model_type != "gpt2"
            || cfg.n_embd == 0
            || cfg.n_head == 0
            || !cfg.n_embd.is_multiple_of(cfg.n_head)
            || cfg.n_layer == 0
            || !["full_dsl", "operation_blocks", "block_plan", "flow_plan"].contains(&cfg.output_mode.as_str())
        {
            return Err(SomError::new(
                "MODEL_UNAVAILABLE",
                "unsupported model configuration",
            ));
        }
        let tokenizer = Tokenizer::from_file(dir.join("tokenizer.json"))
            .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?;
        if tokenizer.get_vocab_size(true) != cfg.vocab_size {
            return Err(SomError::new(
                "MODEL_UNAVAILABLE",
                "tokenizer vocabulary disagrees with model",
            ));
        }
        if cfg.output_mode == "block_plan" || cfg.output_mode == "flow_plan" {
            let codec: Value = serde_json::from_slice(&fs::read(dir.join("codec.json")).map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?)
                .map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?;
            let ids = codec["token_ids"].as_object().ok_or_else(||SomError::new("MODEL_UNAVAILABLE","codec lacks token IDs"))?;
            if ids.len()!=cfg.vocab_size || ids.iter().any(|(s,v)| v.as_u64().is_none_or(|n|n>=cfg.vocab_size as u64 || tokenizer.token_to_id(s)!=Some(n as u32))) {
                return Err(SomError::new("MODEL_UNAVAILABLE","tokenizer and codec token IDs differ"));
            }
            if cfg.output_mode == "flow_plan" {
                if cfg.training_profile == crate::flow_m3_model::PURPOSE {
                    crate::flow_m3_model::verify_config(&config_json)?;
                } else if cfg.training_profile.is_empty() && cfg.flow_decode_policy.is_empty() {
                    crate::flow_model::verify_config(&config_json)?;
                } else {
                    return Err(SomError::new("MODEL_UNAVAILABLE", "unknown flow training profile"));
                }
                crate::flow_model::verify_input_policy(dir, &config_json)?;
                let mut expected = vec!["<pad>".to_owned(), "<eos>".into(), "<map>".into(), "</map>".into(), "<list>".into(), "</list>".into(), "<input>".into(), "<plan>".into()];
                expected.extend((0..1016).map(|index|format!("<v:{index}>")));
                if cfg.vocab_size != 1024 || codec["version"] != crate::flow_codec::COMPACT_VERSION ||
                    expected.iter().enumerate().any(|(index, token)| tokenizer.token_to_id(token)!=Some(index as u32)) {
                    return Err(SomError::new("MODEL_UNAVAILABLE", "flow vocabulary differs from the lossless structural contract"));
                }
                let contracts: Value = serde_json::from_slice(&fs::read(dir.join("contracts.json")).map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?)
                    .map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?;
                if contracts != *crate::flow::COMPACT_CATALOG {
                    return Err(SomError::new("MODEL_UNAVAILABLE", "flow block contracts differ from the compiled validator"));
                }
            }
        }
        let device = Device::new_metal(0)
            .map_err(|e| SomError::new("MODEL_UNAVAILABLE", format!("Metal device: {e}")))?;
        let tensors = candle_core::safetensors::load(dir.join("model.safetensors"), &device)
            .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?;
        let vb = VarBuilder::from_tensors(tensors, DType::F32, &device).pp("model");
        let wte =
            candle_nn::embedding(cfg.vocab_size, cfg.n_embd, vb.pp("wte")).map_err(model_error)?;
        let wpe =
            candle_nn::embedding(cfg.n_positions, cfg.n_embd, vb.pp("wpe")).map_err(model_error)?;
        let layers = (0..cfg.n_layer)
            .map(|i| Layer::load(vb.pp(format!("h.{i}")), &cfg))
            .collect::<CResult<Vec<_>>>()
            .map_err(model_error)?;
        let ln_f = candle_nn::layer_norm(cfg.n_embd, cfg.layer_norm_epsilon, vb.pp("ln_f"))
            .map_err(model_error)?;
        let template = if cfg.output_mode == "operation_blocks" {
            Some(
                serde_json::from_slice(
                    &fs::read(dir.join("dsl_template.json"))
                        .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?,
                )
                .map_err(|e| SomError::new("MODEL_UNAVAILABLE", e.to_string()))?,
            )
        } else {
            None
        };
        let legacy = if cfg.output_mode == "block_plan" || cfg.output_mode == "flow_plan" {
            Some(Box::new(Self::load(&dir.join("legacy"))?))
        } else { None };
        let catalog = if cfg.output_mode == "block_plan" || cfg.output_mode == "flow_plan" {
            Some(serde_json::from_slice(&fs::read(dir.join("contracts.json")).map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?).map_err(|e|SomError::new("MODEL_UNAVAILABLE",e.to_string()))?)
        } else {None};
        Ok(Self {
            config: cfg,
            catalog,
            tokenizer,
            device,
            wte,
            wpe,
            layers,
            ln_f,
            template,
            legacy,
        })
    }

    fn logits(
        &self,
        ids: &[u32],
        offset: usize,
        cache: &mut [Option<(Tensor, Tensor)>],
    ) -> CResult<Tensor> {
        let seq = ids.len();
        let tokens = Tensor::new(ids, &self.device)?.unsqueeze(0)?;
        let positions =
            Tensor::arange(offset as u32, (offset + seq) as u32, &self.device)?.unsqueeze(0)?;
        let mut x = (&self.wte.forward(&tokens)? + &self.wpe.forward(&positions)?)?;
        let total = offset + seq;
        let mask_data: Vec<f32> = (0..seq)
            .flat_map(|i| (0..total).map(move |j| if j > offset + i { -1e9 } else { 0.0 }))
            .collect();
        let mask = Tensor::from_vec(mask_data, (1, 1, seq, total), &self.device)?;
        for (layer, layer_cache) in self.layers.iter().zip(cache.iter_mut()) {
            x = layer.forward(&x, self.config.n_head, &mask, layer_cache)?;
        }
        let hidden = metal_layer_norm(&self.ln_f, &x)?;
        hidden
            .i((0, seq - 1))?
            .unsqueeze(0)?
            .matmul(&self.wte.embeddings().t()?)?
            .squeeze(0)
    }

    pub fn generate(&self, prompt: &str) -> Result<String, SomError> {
        if self.config.output_mode == "flow_plan" {
            let parsed: Value = serde_json::from_str(prompt).map_err(|e|SomError::new("INVALID_JSON",e.to_string()))?;
            if parsed["version"] != crate::flow::VERSION {
                return self.legacy.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing frozen legacy decoder"))?.generate(prompt);
            }
            return self.generate_flow_plan(&parsed);
        }
        if self.config.output_mode == "block_plan" {
            let p: Value = serde_json::from_str(prompt).map_err(|e| SomError::new("INVALID_JSON", e.to_string()))?;
            if p["version"] != crate::composed::VERSION {
                return self.legacy.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing frozen legacy decoder"))?.generate(prompt);
            }
            return self.generate_block_plan(&p);
        }
        if self.config.output_mode == "operation_blocks" {
            return self.generate_operation_blocks(prompt);
        }
        let started = Instant::now();
        let input = format!("<prompt>{prompt}</prompt><dsl>");
        let encoding = self
            .tokenizer
            .encode(input, false)
            .map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
        let mut ids = encoding.get_ids().to_vec();
        if ids.is_empty() || ids.len() >= self.config.n_ctx {
            return Err(SomError::new(
                "INVALID_VALUE",
                "prompt exceeds model context",
            ));
        }
        let eos = self
            .tokenizer
            .token_to_id("<eos>")
            .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "tokenizer lacks <eos>"))?;
        let start = ids.len();
        let mut cache = vec![None; self.layers.len()];
        let mut next = self
            .logits(&ids, 0, &mut cache)
            .map_err(model_error)?
            .argmax(0)
            .map_err(model_error)?
            .to_scalar::<u32>()
            .map_err(model_error)?;
        for _ in 0..self.config.max_new_tokens {
            if started.elapsed() >= Duration::from_secs(60) {
                return Err(SomError::new(
                    "MODEL_TIMEOUT",
                    "generation exceeded 60 seconds",
                ));
            }
            if ids.len() >= self.config.n_ctx {
                break;
            }
            if next == eos {
                break;
            }
            ids.push(next);
            if ids.len() < self.config.n_ctx {
                next = self
                    .logits(&[next], ids.len() - 1, &mut cache)
                    .map_err(model_error)?
                    .argmax(0)
                    .map_err(model_error)?
                    .to_scalar::<u32>()
                    .map_err(model_error)?;
            }
        }
        self.tokenizer
            .decode(&ids[start..], false)
            .map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))
    }

    fn flow_features(&self, prompt: &Value, catalog: &Value) -> Result<String, SomError> {
        match self.config.flow_input_policy.as_str() {
            "" => crate::flow_codec::features_with(prompt, catalog),
            crate::flow_codec::M3_INPUT_VERSION => crate::flow_codec::features_m3_with(prompt, catalog),
            crate::flow_codec::M3_SEMANTIC_INPUT_VERSION => crate::flow_codec::features_m3_semantic_with(prompt, catalog),
            crate::flow_codec::M3_BOUND_INPUT_VERSION => crate::flow_codec::features_m3_bound_with(prompt, catalog),
            _ => Err(SomError::new("MODEL_UNAVAILABLE", "unknown flow input policy")),
        }
    }

    fn generate_flow_plan(&self, prompt: &Value) -> Result<String, SomError> {
        if self.config.training_profile == crate::flow_m3_model::PURPOSE {
            return self.diagnostic_flow_sequence(prompt, true);
        }
        let catalog = self.catalog.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing flow contracts"))?;
        let input = self.flow_features(prompt, catalog)?;
        let encoded = self.tokenizer.encode(input.as_str(), false).map_err(|e|SomError::new("MODEL_FAILED", e.to_string()))?;
        let ids = encoded.get_ids();
        if ids.is_empty() || ids.len()>crate::flow_codec::LIMIT || ids.len()>=self.config.n_ctx {
            return Err(SomError::new("SCOPE_TOO_LARGE", "flow input exceeds model bounds"));
        }
        let eos = self.tokenizer.token_to_id("<eos>").ok_or_else(||SomError::new("MODEL_UNAVAILABLE", "missing flow end token"))?;
        let started = Instant::now();
        let mut cache = vec![None; self.layers.len()];
        let mut logits = self.logits(ids, 0, &mut cache).map_err(model_error)?;
        let mut output = Vec::new();
        for position in 0..self.config.max_new_tokens.min(crate::flow_codec::LIMIT) {
            if started.elapsed()>=Duration::from_secs(60) {
                return Err(SomError::new("MODEL_TIMEOUT", "flow sequence exceeded 60 seconds"));
            }
            let next = logits.argmax(0).map_err(model_error)?.to_scalar::<u32>().map_err(model_error)?;
            output.push(next);
            if next == eos {
                let text = self.tokenizer.decode(&output, false).map_err(|e|SomError::new("MODEL_FAILED", e.to_string()))?;
                eprintln!("{}",serde_json::json!({"stage":"flow_decoded_plan","actual_input":input,"input_ids":ids,"output_ids":output,"raw_output":text,"inference_ms":started.elapsed().as_secs_f64()*1000.0}));
                let dsl = crate::flow_codec::decode_with(prompt,&text,catalog)?;
                return serde_json::to_string(&dsl).map_err(|e|SomError::new("INVALID_DSL",e.to_string()));
            }
            if ids.len()+position+1>=self.config.n_ctx {break;}
            logits = self.logits(&[next],ids.len()+position,&mut cache).map_err(model_error)?;
        }
        eprintln!("{}",serde_json::json!({"stage":"flow_incomplete_plan","actual_input":input,"input_ids":ids,"output_ids":output}));
        Err(SomError::new("MODEL_FAILED", "flow sequence lacks an explicit end within output bounds"))
    }

    /// Run the identical native weights with or without public prefix contracts.
    /// The unconstrained branch is an evaluation control, never a fallback.
    pub fn diagnostic_flow_sequence(&self, prompt: &Value, constrained: bool) -> Result<String, SomError> {
        if self.config.output_mode != "flow_plan" {
            return Err(SomError::new("MODEL_UNAVAILABLE", "flow trace needs flow_plan weights"));
        }
        let catalog = self.catalog.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing flow contracts"))?;
        let input = self.flow_features(prompt, catalog)?;
        let encoded = self.tokenizer.encode(input.as_str(), false).map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
        let ids = encoded.get_ids();
        if ids.is_empty() || ids.len() > 2048 { return Err(SomError::new("SCOPE_TOO_LARGE", "flow input exceeds model bounds")); }
        let mut state = crate::flow_constraints::State::new(prompt, catalog)?;
        let mut output = Vec::new();
        let mut choices = Vec::new();
        let mut cache = vec![None; self.layers.len()];
        let started = Instant::now();
        let mut logits = self.logits(ids, 0, &mut cache).map_err(model_error)?;
        let execution = (|| {
            for position in 0..self.config.max_new_tokens.min(2048) {
                if started.elapsed() >= Duration::from_secs(60) {
                    return Err(SomError::new("MODEL_TIMEOUT", "flow sequence exceeded 60 seconds"));
                }
                let scores = logits.to_vec1::<f32>().map_err(model_error)?;
                if scores.iter().any(|score| !score.is_finite()) {
                    return Err(SomError::new("MODEL_FAILED", "flow logits contain non-finite values"));
                }
                let raw = logits.argmax(0).map_err(model_error)?.to_scalar::<u32>().map_err(model_error)?;
                let next = if constrained {
                    let allowed = state.allowed()?;
                    let chosen = allowed.iter().copied().max_by(|left, right|
                        scores[*left as usize].total_cmp(&scores[*right as usize]).then_with(|| right.cmp(left)))
                        .ok_or_else(|| SomError::new("MODEL_FAILED", "flow prefix has no legal next token"))?;
                    choices.push(serde_json::json!({"position":position,"raw_top1":raw,
                        "selected":chosen,"allowed":allowed,"forced":allowed.len()==1,
                        "context":state.context()}));
                    state.advance_checked(chosen)?;
                    chosen
                } else { raw };
                output.push(next);
                if next == 1 {
                    let text = self.tokenizer.decode(&output, false).map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
                    let dsl = crate::flow_codec::decode_with(prompt, &text, catalog)?;
                    return serde_json::to_string(&dsl).map_err(|e| SomError::new("INVALID_DSL", e.to_string()));
                }
                if ids.len() + position + 1 >= self.config.n_ctx { break; }
                logits = self.logits(&[next], ids.len()+position, &mut cache).map_err(model_error)?;
            }
            Err(SomError::new("MODEL_FAILED", "flow sequence lacks an explicit end within output bounds"))
        })();
        let raw_output = self.tokenizer.decode(&output, false).unwrap_or_default();
        eprintln!("{}", serde_json::json!({"stage":"flow_decoded_plan","actual_input":input,
            "input_ids":ids,"output_ids":output,"raw_output":raw_output,
            "decode_mode":if constrained {"constrained"} else {"unconstrained"},
            "decode_policy":crate::flow_constraints::VERSION,"choices":choices,
            "refusal":execution.as_ref().err().map(|error| serde_json::json!({"code":error.code,"detail":error.detail})),
            "inference_ms":started.elapsed().as_secs_f64()*1000.0}));
        execution
    }

    fn generate_block_plan(&self, prompt: &Value) -> Result<String, SomError> {
        let input = crate::composed::features(prompt)?;
        let encoded = self.tokenizer.encode(input, false).map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
        let ids = encoded.get_ids();
        if ids.is_empty() || ids.len() > 2048 { return Err(SomError::new("SCOPE_TOO_LARGE", "block plan input exceeds 2048 tokens")); }
        let eos = self.tokenizer.token_to_id("<eos>").ok_or_else(||SomError::new("MODEL_UNAVAILABLE", "missing plan end token"))?;
        let started = Instant::now();
        let mut cache = vec![None; self.layers.len()];
        let mut logits = self.logits(ids, 0, &mut cache).map_err(model_error)?;
        let mut output = Vec::new();
        for step in 0..self.config.max_new_tokens.min(2048) {
            if started.elapsed() >= Duration::from_secs(60) { return Err(SomError::new("MODEL_TIMEOUT", "block plan exceeded 60 seconds")); }
            let next = logits.argmax(0).map_err(model_error)?.to_scalar::<u32>().map_err(model_error)?;
            output.push(next);
            if next == eos {
                let inference_ms = started.elapsed().as_secs_f64()*1000.0;
                let validation_started = Instant::now();
                let text = self.tokenizer.decode(&output, false).map_err(|e|SomError::new("MODEL_FAILED",e.to_string()))?;
                eprintln!("{}", serde_json::json!({"stage":"decoded_plan", "tokens":output, "text":text}));
                let dsl = crate::composed::decode(&text)?;
                crate::composed::validate_plan_with(prompt, &dsl, self.catalog.as_ref().unwrap())?;
                eprintln!("{}", serde_json::json!({"stage":"model","input_tokens":ids.len(),"output_tokens":output.len(),"inference_ms":inference_ms,"decode_validation_ms":validation_started.elapsed().as_secs_f64()*1000.0}));
                return serde_json::to_string(&dsl).map_err(|e|SomError::new("INVALID_DSL",e.to_string()));
            }
            if ids.len()+step+1 >= self.config.n_ctx { break; }
            logits = self.logits(&[next], ids.len()+step, &mut cache).map_err(model_error)?;
        }
        Err(SomError::new("MODEL_FAILED", "block plan did not end within the output limit"))
    }

    fn generate_operation_blocks(&self, prompt: &str) -> Result<String, SomError> {
        let prompt: Value = serde_json::from_str(prompt)
            .map_err(|e| SomError::new("INVALID_VALUE", e.to_string()))?;
        crate::validate_prompt(&prompt)?;
        let version = prompt["version"].as_str().unwrap();
        let (key, selections) = if version == crate::related::VERSION {
            (crate::related::template_key(&prompt), crate::related::selections(&prompt))
        } else {
            let (steps, list): (&[&str], &str) = match version {
                "som.prompt.v1" => (&LOGIC_STEPS, "api.list"),
                "som.prompt.v2" => (&LOGIC_STEPS_V2, "api.list.query"),
                "som.prompt.v3" => (&LOGIC_STEPS_V3, "api.list.boolean"),
                "som.prompt.v4" => (&LOGIC_STEPS_V4, "api.list.boolean.date"),
                _ => return Err(SomError::new("UNSUPPORTED_VERSION", "unsupported prompt version")),
            };
            let ids = ["api.create", "api.get", list, "api.patch", "api.delete"];
            let slots = ["create", "get", "list", "patch", "delete"];
            let selections = prompt["logic"].as_array().unwrap().iter().map(|step| {
                let text = step.as_str().unwrap();
                let index = steps.iter().position(|value| *value == text).unwrap();
                (slots[index].to_owned(), ids[index].to_owned(), format!("<step>{text}</step><block>"))
            }).collect::<Vec<_>>();
            (version.to_owned(), selections)
        };
        let mut operations: Vec<String> = ALL_OPERATIONS.iter().map(|s| s.to_string()).collect();
        operations.extend(crate::related::operations());
        let started = Instant::now();
        for (_, expected, input) in &selections {
            if started.elapsed() >= Duration::from_secs(60) {
                return Err(SomError::new("MODEL_TIMEOUT", "generation exceeded 60 seconds"));
            }
            let encoding = self.tokenizer.encode(input.as_str(), false)
                .map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
            let ids = encoding.get_ids();
            if ids.is_empty() || ids.len() >= self.config.n_ctx {
                return Err(SomError::new("INVALID_VALUE", "logic step exceeds model context"));
            }
            let mut cache = vec![None; self.layers.len()];
            let logits = self.logits(ids, 0, &mut cache).map_err(model_error)?;
            // Read all scores once rather than synchronize for every candidate.
            let scores = logits.to_vec1::<f32>().map_err(model_error)?;
            let selected = operations.iter().filter_map(|op| {
                self.tokenizer.token_to_id(&format!("<{op}>")).map(|id| (op, scores[id as usize]))
            }).max_by(|a, b| a.1.total_cmp(&b.1)).map(|(op, _)| op);
            if selected != Some(expected) {
                return Err(SomError::new("MODEL_FAILED", "model selected a block that does not fit the scoped step"));
            }
        }
        let templates = self.template.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing DSL template"))?;
        let mut dsl = templates.get(&key)
            .or_else(|| (version == "som.prompt.v1" && templates.get("files").is_some()).then_some(templates))
            .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing DSL template for prompt"))?.clone();
        let files = dsl["files"].as_array_mut().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "invalid DSL template"))?;
        let file = files.iter_mut().find(|f| f["blocks"].as_array().is_some_and(|blocks| blocks.iter().any(|b| b["id"] == "api.head" || b["id"] == "api.related.head" || b["id"].as_str().is_some_and(|id| id.starts_with("api.business.head.")))))
            .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks named API slots"))?;
        let blocks = file["blocks"].as_array_mut().unwrap();
        let by_name: std::collections::HashMap<String, Value> = blocks.iter().map(|b| (b["name"].as_str().unwrap_or("").to_owned(), b.clone())).collect();
        let mut ordered = vec![by_name.get("head").ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks API head"))?.clone()];
        for (slot, id, _) in selections {
            let block = by_name.get(&slot).filter(|b| b["id"] == id)
                .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks operation slot"))?;
            ordered.push(block.clone());
        }
        if let Some(workflow) = by_name.get("workflow") { ordered.push(workflow.clone()); }
        ordered.push(by_name.get("tail").ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks API tail"))?.clone());
        if let Some(app) = by_name.get("app") { ordered.push(app.clone()); }
        *blocks = ordered;
        serde_json::to_string(&dsl).map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))
    }
}

impl Decoder {
    /// Offline evidence only. The preserved production decoder is unchanged.
    pub fn diagnostic_operation_trace(&self, prompt: &str) -> Result<Value, SomError> {
        let prompt: Value = serde_json::from_str(prompt)
            .map_err(|e| SomError::new("INVALID_VALUE", e.to_string()))?;
        crate::validate_prompt(&prompt)?;
        let version = prompt["version"].as_str().unwrap();
        let (key, selections) = if version == crate::related::VERSION {
            (crate::related::template_key(&prompt), crate::related::selections(&prompt))
        } else {
            let (steps, list): (&[&str], &str) = match version {
                "som.prompt.v1" => (&LOGIC_STEPS, "api.list"),
                "som.prompt.v2" => (&LOGIC_STEPS_V2, "api.list.query"),
                "som.prompt.v3" => (&LOGIC_STEPS_V3, "api.list.boolean"),
                "som.prompt.v4" => (&LOGIC_STEPS_V4, "api.list.boolean.date"),
                _ => return Err(SomError::new("UNSUPPORTED_VERSION", "unsupported prompt version")),
            };
            let ids = ["api.create", "api.get", list, "api.patch", "api.delete"];
            let slots = ["create", "get", "list", "patch", "delete"];
            let selections = prompt["logic"].as_array().unwrap().iter().map(|step| {
                let text = step.as_str().unwrap();
                let index = steps.iter().position(|value| *value == text).unwrap();
                (slots[index].to_owned(), ids[index].to_owned(), format!("<step>{text}</step><block>"))
            }).collect::<Vec<_>>();
            (version.to_owned(), selections)
        };
        let mut operations: Vec<String> = ALL_OPERATIONS.iter().map(|s| s.to_string()).collect();
        operations.extend(crate::related::operations());
        let mut observed = Vec::new();
        let started = Instant::now();
        for (_, expected, input) in &selections {
            if started.elapsed() >= Duration::from_secs(60) {
                return Err(SomError::new("MODEL_TIMEOUT", "generation exceeded 60 seconds"));
            }
            let encoding = self.tokenizer.encode(input.as_str(), false)
                .map_err(|e| SomError::new("MODEL_FAILED", e.to_string()))?;
            let ids = encoding.get_ids();
            if ids.is_empty() || ids.len() >= self.config.n_ctx {
                return Err(SomError::new("INVALID_VALUE", "logic step exceeds model context"));
            }
            let mut cache = vec![None; self.layers.len()];
            let logits = self.logits(ids, 0, &mut cache).map_err(model_error)?;
            // Read all scores once rather than synchronize for every candidate.
            let scores = logits.to_vec1::<f32>().map_err(model_error)?;
            let selected = operations.iter().filter_map(|op| {
                self.tokenizer.token_to_id(&format!("<{op}>")).map(|id| (op, scores[id as usize]))
            }).max_by(|a, b| a.1.total_cmp(&b.1)).map(|(op, _)| op);
            let selected_id = selected.and_then(|op| self.tokenizer.token_to_id(&format!("<{op}>")));
            observed.push(serde_json::json!({"input":input, "input_token_ids":ids, "raw_output_token":selected.map(|op|format!("<{op}>")), "output_token_id":selected_id}));
            if selected != Some(expected) {
                return Err(SomError::new("MODEL_FAILED", "model selected a block that does not fit the scoped step"));
            }
        }
        let templates = self.template.as_ref().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing DSL template"))?;
        let mut dsl = templates.get(&key)
            .or_else(|| (version == "som.prompt.v1" && templates.get("files").is_some()).then_some(templates))
            .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "missing DSL template for prompt"))?.clone();
        let files = dsl["files"].as_array_mut().ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "invalid DSL template"))?;
        let file = files.iter_mut().find(|f| f["blocks"].as_array().is_some_and(|blocks| blocks.iter().any(|b| b["id"] == "api.head" || b["id"] == "api.related.head" || b["id"].as_str().is_some_and(|id| id.starts_with("api.business.head.")))))
            .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks named API slots"))?;
        let blocks = file["blocks"].as_array_mut().unwrap();
        let by_name: std::collections::HashMap<String, Value> = blocks.iter().map(|b| (b["name"].as_str().unwrap_or("").to_owned(), b.clone())).collect();
        let mut ordered = vec![by_name.get("head").ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks API head"))?.clone()];
        for (slot, id, _) in selections {
            let block = by_name.get(&slot).filter(|b| b["id"] == id)
                .ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks operation slot"))?;
            ordered.push(block.clone());
        }
        if let Some(workflow) = by_name.get("workflow") { ordered.push(workflow.clone()); }
        ordered.push(by_name.get("tail").ok_or_else(|| SomError::new("MODEL_UNAVAILABLE", "template lacks API tail"))?.clone());
        if let Some(app) = by_name.get("app") { ordered.push(app.clone()); }
        *blocks = ordered;
        Ok(serde_json::json!({"inference":observed,"dsl":dsl,"milliseconds":started.elapsed().as_secs_f64()*1000.0}))
    }
}


fn model_error(err: candle_core::Error) -> SomError {
    SomError::new("MODEL_FAILED", err.to_string())
}

// Candle's fused layer-norm kernel is unavailable on Metal in the tested build.
// Use the same unfused formula on the GPU, including the learned scale and bias.
fn metal_layer_norm(layer: &LayerNorm, input: &Tensor) -> CResult<Tensor> {
    let width = input.dims3()?.2 as f64;
    let mean = (input.sum_keepdim(2)? / width)?;
    let centered = input.broadcast_sub(&mean)?;
    let variance = (centered.sqr()?.sum_keepdim(2)? / width)?;
    let scaled = centered
        .broadcast_div(&(variance + layer.eps())?.sqrt()?)?
        .broadcast_mul(layer.weight())?;
    match layer.bias() {
        Some(bias) => scaled.broadcast_add(bias),
        None => Ok(scaled),
    }
}
