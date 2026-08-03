use super::*;

fn sudhir_preset(model: &str, display_name: &str, provider: &str) -> ModelPreset {
    ModelPreset {
        id: model.to_string(),
        model: model.to_string(),
        display_name: display_name.to_string(),
        description: format!("Open model served by {provider}"),
        default_reasoning_effort: ReasoningEffortConfig::High,
        supported_reasoning_efforts: vec![ReasoningEffortPreset {
            effort: ReasoningEffortConfig::High,
            description: "high".to_string(),
        }],
        supports_personality: false,
        additional_speed_tiers: Vec::new(),
        service_tiers: Vec::new(),
        default_service_tier: None,
        is_default: false,
        upgrade: None,
        show_in_picker: true,
        multi_agent_version: None,
        availability_nux: None,
        supported_in_api: true,
        input_modalities: default_input_modalities(),
    }
}

#[tokio::test]
async fn picker_searches_merged_model_names_and_providers() {
    let (mut chat, _rx, _op_rx) = make_chatwidget_manual(Some("pi-deepseek/v4-flash")).await;
    chat.thread_id = Some(ThreadId::new());
    chat.open_model_popup_with_presets(vec![
        sudhir_preset("gpt-5.6-sol", "GPT 5.6 Sol", "openai"),
        sudhir_preset("pi-deepseek/v4-flash", "DeepSeek V4 Flash", "deepseek"),
        sudhir_preset("cursor/composer-2.5", "Composer 2.5", "cursor"),
    ]);

    for character in "cursor".chars() {
        chat.handle_key_event(KeyEvent::from(KeyCode::Char(character)));
    }
    let popup = render_bottom_popup(&chat, /*width*/ 100);
    assert!(popup.contains("cursor/composer-2.5"));
    assert!(!popup.contains("pi-deepseek/v4-flash"));
    assert!(!popup.contains("gpt-5.6-sol"));
}

#[tokio::test]
async fn max_and_ultra_are_visible_and_keyboard_reachable() {
    let (mut chat, mut rx, _op_rx) = make_chatwidget_manual(Some("gpt-5.4")).await;
    chat.thread_id = Some(ThreadId::new());
    let mut preset = get_available_model(&chat, "gpt-5.4");
    preset.supported_reasoning_efforts.extend([
        ReasoningEffortPreset {
            effort: ReasoningEffortConfig::Max,
            description: "Maximum reasoning".to_string(),
        },
        ReasoningEffortPreset {
            effort: ReasoningEffortConfig::Ultra,
            description: "Ultra reasoning".to_string(),
        },
    ]);
    chat.model_catalog = std::sync::Arc::new(ModelCatalog::new(vec![preset]));
    chat.set_model("gpt-5.4");

    for (current, expected) in [
        (ReasoningEffortConfig::XHigh, ReasoningEffortConfig::Max),
        (ReasoningEffortConfig::Max, ReasoningEffortConfig::Ultra),
    ] {
        chat.set_reasoning_effort(Some(current));
        chat.handle_key_event(KeyEvent::new(KeyCode::Char('.'), KeyModifiers::ALT));
        let events = std::iter::from_fn(|| rx.try_recv().ok()).collect::<Vec<_>>();
        assert!(events.iter().any(|event| matches!(
            event,
            AppEvent::UpdateActiveReasoningEffort(Some(effort)) if effort == &expected
        )));
        assert!(
            events
                .iter()
                .all(|event| !matches!(event, AppEvent::PersistModelSelection { .. }))
        );
    }
}
