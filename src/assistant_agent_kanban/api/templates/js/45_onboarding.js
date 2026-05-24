(() => {
  // Ensure the page is loaded before checking onboarding
  window.addEventListener('DOMContentLoaded', () => {
    // Get elements
    const overlay = document.getElementById('onboarding-overlay');
    const card = overlay ? overlay.querySelector('.onboarding-card') : null;
    const skipBtn = document.getElementById('onboarding-skip-btn');
    const prevBtn = document.getElementById('onboarding-prev-btn');
    const nextBtn = document.getElementById('onboarding-next-btn');

    if (!overlay || !card || !skipBtn || !prevBtn || !nextBtn) {
      console.warn('Onboarding elements not found on the page.');
      return;
    }

    let currentStep = 1;
    const totalSteps = 6;
    let activePointerBadges = [];

    // Helper to get translation dictionary values
    function getOnboardingText(key) {
      const languageEl = document.getElementById('runtime_language');
      const lang = (languageEl && languageEl.value === 'KO') ? 'KO' : 'EN';
      if (window.settingsTranslations) {
        const table = window.settingsTranslations[lang] || window.settingsTranslations.EN;
        return table[key] || window.settingsTranslations.EN[key] || '';
      }
      // Fallback in case translations are missing
      const fallbacks = {
        KO: {
          onboardingSkip: '건너뛰기',
          onboardingPrev: '이전',
          onboardingNext: '다음',
          onboardingFinish: '완료',
          onboardingStep1Title: '반갑습니다! 👋',
          onboardingStep1Desc: 'Assistant Agent Kanban을 효율적으로 사용하기 위한 설정 및 새 요청 등록 방법을 쉽고 빠르게 안내해 드릴게요.',
          onboardingStep2Title: '런타임 역할 별 모델 🤖',
          onboardingStep2Desc: '각 실행 역할(플래너, 구현기, 리뷰어 등)별 기본 어시스턴트 엔진 및 AI 모델을 지정합니다.<br><br>💡 <strong>주요 설정 안내</strong>:<br><ul class="onboarding-guide-list"><li><strong>각 런타임 설정</strong>: 플래너, 구현기, 리뷰어 등 각 실행 단계마다 개별적으로 어시스턴트와 모델을 지정할 수 있습니다.</li><li><strong>어시스턴트 및 모델 선택</strong>: 각 단계에 활용할 어시스턴트 툴(Antigravity, Codex, Claude, Gemini, OpenCode)과 그에 맞는 AI 모델을 선택합니다.</li><li><strong>에이전트 수 (최대 실행 개수)</strong>: 카드 좌측의 숫자 필드를 통해 동시에 실행할 에이전트의 개수(병렬 슬롯)를 제어합니다.</li></ul>',
          onboardingStep3Title: '슬랙 연동 💬',
          onboardingStep3Desc: '실시간 진행 상황 알림 및 인간 개입형 승인(Human-in-the-loop) 알림을 수신할 슬랙 연동을 설정합니다.<br><br>💡 <strong>쉽고 빠른 연동 단계</strong>:<br><ul class="onboarding-guide-list"><li class="custom-bullet">1️⃣ <strong>연동 활성화</strong>: "슬랙 연동 활성화" 스위치를 활성화합니다.</li><li class="custom-bullet">2️⃣ <strong>토큰 입력</strong>: Slack App 설정에서 발급받은 Bot OAuth Token (<code>xoxb-...</code>)을 입력합니다.</li><li class="custom-bullet">3️⃣ <strong>채널 설정</strong>: 알림을 수신할 슬랙 채널명(예: <code>#agent-alerts</code>)을 지정합니다.</li><li class="custom-bullet">4️⃣ <strong>연동 검증</strong>: "Slack 테스트 및 채널 생성" 버튼을 클릭해 연동 상태를 확인하고 저장합니다.</li></ul>',
          onboardingStep4Title: '새 요청 등록 🚀',
          onboardingStep4Desc: '대시보드 상단의 "새 요청" 버튼을 클릭하여 작업 생성 대화창을 엽니다.',
          onboardingStep5Title: '대상 프로젝트 지정 📂',
          onboardingStep5Desc: '어시스턴트와 초안을 다듬기 전에, 먼저 이 요청의 대상 프로젝트를 지정합니다.<br><br>💡 <strong>대상 프로젝트가 중요한 이유</strong>:<br><ul class="onboarding-guide-list"><li><strong>범위 자동 채움</strong>: 선택한 프로젝트 경로를 기반으로 범위(Scope)와 범위 외(Out of Scope) 필드가 자동으로 채워집니다.</li><li><strong>브랜치 제안</strong>: 선택한 저장소에서 브랜치 자동 완성이 로드되어 올바른 기준 브랜치를 쉽게 선택할 수 있습니다.</li><li><strong>AI 컨텍스트</strong>: 어시스턴트가 대상 저장소의 프로젝트별 파일(<code>AGENTS.md</code>, <code>CLAUDE.md</code>)을 읽어 더 정확한 초안을 생성합니다.</li></ul>',
          onboardingStep6Title: '어시스턴트 초안 다듬기 ✍️',
          onboardingStep6Desc: '자연어로 요구사항을 자유롭게 입력하면, AI 어시스턴트가 분석하여 자동으로 정교한 요청서 초안(REQUEST.md)을 작성해 줍니다.<br><br>💡 <strong>초안 작성 안내</strong>:<br><ul class="onboarding-guide-list"><li><strong>자연어 대화</strong>: "로그인 기능 추가해줘"와 같이 간단히 입력하고 전송하면 AI가 목표와 범위를 명확히 규정합니다.</li><li><strong>이미지 첨부 지원</strong>: 디자인 시안이나 버그 스크린샷 이미지를 첨부하여 더욱 명확한 컨텍스트를 제공할 수 있습니다.</li><li><strong>자동 필드 동기화</strong>: 대화 내용을 기반으로 목표, 수용 기준, 제외 대상 등의 상세 필드가 실시간으로 자동 동기화됩니다.</li></ul>',
          badgeMaxExecutions: '동시 실행 수(병렬 슬롯) 설정',
          badgeAssistantModel: '어시스턴트 툴 및 모델 선택',
          badgeRuntimeTitle: '각 런타임별 개별 모델 구성',
          badgeSlackEnable: '1. 슬랙 연동 활성화 스위치 ON',
          badgeSlackToken: '2. Bot OAuth 토큰(xoxb-...) 입력',
          badgeSlackChannel: '3. 알림 수신할 슬랙 채널 지정',
          badgeSlackTest: '4. 연동 상태 테스트 및 설정 저장',
          badgeNewRequest: '여기에서 첫 워크플로우를 시작해 보세요!',
          badgeTargetRepo: '대상 프로젝트 저장소를 찾아 선택하세요',
          badgeBaseBranch: '선택한 저장소에서 브랜치가 자동 제안됩니다',
          badgeRequestDraftInput: '원하는 기능이나 작업 사항을 자연어로 입력',
          badgeRequestDraftSend: '피드백을 전송하여 초안 작성 시작',
          badgeRequestDraftTranscript: '어시스턴트 대화 및 분석 상태 표시 영역'
        },
        EN: {
          onboardingSkip: 'Skip',
          onboardingPrev: 'Prev',
          onboardingNext: 'Next',
          onboardingFinish: 'Finish',
          onboardingStep1Title: 'Welcome to Assistant Agent Kanban! 👋',
          onboardingStep1Desc: 'We will guide you through the settings and how to create a new task.',
          onboardingStep2Title: 'Models by Runtime Role 🤖',
          onboardingStep2Desc: 'Map specialized assistant drivers and AI models to each executor role (Planner, Implementer, Reviewer, etc.).<br><br>💡 <strong>Configuration Guide</strong>:<br><ul class="onboarding-guide-list"><li><strong>Individual Runtimes</strong>: Configure customized assistants and models for each individual stage like Planner, Implementer, or Reviewer.</li><li><strong>Select Assistant & Model</strong>: Choose the target assistant tool (Antigravity, Codex, Claude, Gemini, OpenCode) and its active AI model.</li><li><strong>Max Executions (Agent count)</strong>: Use the number input on the left of each card to control the maximum parallel execution slots.</li></ul>',
          onboardingStep3Title: 'Slack Integration 💬',
          onboardingStep3Desc: 'Configure Slack integration to receive real-time execution progress updates and handle Human-in-the-loop approval workflows.<br><br>💡 <strong>Easy Integration Steps</strong>:<br><ul class="onboarding-guide-list"><li class="custom-bullet">1️⃣ <strong>Enable Integration</strong>: Toggle the "Enable Slack Integration" switch to ON.</li><li class="custom-bullet">2️⃣ <strong>Enter Token</strong>: Paste your Bot OAuth Token (<code>xoxb-...</code>) obtained from your Slack App configuration.</li><li class="custom-bullet">3️⃣ <strong>Set Channel</strong>: Specify the Slack channel name (e.g., <code>#agent-alerts</code>) to receive agent notifications.</li><li class="custom-bullet">4️⃣ <strong>Verify Connection</strong>: Click the "Test Slack & Create Channel" button to verify the integration and save.</li></ul>',
          onboardingStep4Title: 'Create a New Request 🚀',
          onboardingStep4Desc: 'Click the "New request" button in the dashboard header to open the request composer modal.',
          onboardingStep5Title: 'Select Target Project 📂',
          onboardingStep5Desc: 'Before drafting with the assistant, first specify the target project for this request.<br><br>💡 <strong>Why this matters</strong>:<br><ul class="onboarding-guide-list"><li><strong>Scope auto-fill</strong>: The Scope and Out of Scope fields are automatically populated based on the selected project path.</li><li><strong>Branch suggestions</strong>: Branch autocomplete loads from the selected repository so you can pick the right base branch.</li><li><strong>AI context</strong>: The assistant reads project-specific files (<code>AGENTS.md</code>, <code>CLAUDE.md</code>) from the target repo to generate more accurate drafts.</li></ul>',
          onboardingStep6Title: 'Draft with Assistant ✍️',
          onboardingStep6Desc: 'Describe what you want to build in natural language, and the AI Assistant will automatically refine and draft a high-quality REQUEST.md for you.<br><br>💡 <strong>Composer Guide</strong>:<br><ul class="onboarding-guide-list"><li><strong>Natural Language Input</strong>: Simply type something like "Add a login screen" and click send. The AI structures your goal and scope.</li><li><strong>Image Attachments</strong>: Drop or attach UI mockups or bug screenshots to provide rich visual context.</li><li><strong>Automatic Field Sync</strong>: The detailed fields (Goal, Acceptance Criteria, Out of Scope) are synchronized automatically in real-time as you chat.</li></ul>',
          badgeMaxExecutions: 'Set max executions (parallel slots)',
          badgeAssistantModel: 'Select assistant tool & model',
          badgeRuntimeTitle: 'Configure individual models for each runtime',
          badgeSlackEnable: '1. Toggle Slack Integration ON',
          badgeSlackToken: '2. Enter Bot OAuth Token (xoxb-...)',
          badgeSlackChannel: '3. Enter destination channel name',
          badgeSlackTest: '4. Test connection and save settings',
          badgeNewRequest: 'Start your first workflow here!',
          badgeTargetRepo: 'Browse and select the target project repository',
          badgeBaseBranch: 'Branch is auto-suggested from the selected repo',
          badgeRequestDraftInput: 'Describe features or changes in plain natural language',
          badgeRequestDraftSend: 'Send your message to start drafting',
          badgeRequestDraftTranscript: 'View the interactive chat transcript and drafting state'
        }
      };
      const table = fallbacks[lang] || fallbacks.EN;
      return table[key] || fallbacks.EN[key] || '';
    }

    // Apply translation texts dynamically
    function applyTranslations() {
      // Dynamic skip title
      skipBtn.title = getOnboardingText('onboardingSkip');

      // Steps texts
      for (let i = 1; i <= totalSteps; i++) {
        const titleEl = document.getElementById(`onboarding-title-${i}`);
        const descEl = document.getElementById(`onboarding-desc-${i}`);
        if (titleEl) titleEl.textContent = getOnboardingText(`onboardingStep${i}Title`);
        if (descEl) descEl.innerHTML = getOnboardingText(`onboardingStep${i}Desc`);
      }

      // Buttons
      prevBtn.textContent = getOnboardingText('onboardingPrev');
      if (currentStep === totalSteps) {
        nextBtn.textContent = getOnboardingText('onboardingFinish');
      } else {
        nextBtn.textContent = getOnboardingText('onboardingNext');
      }
    }

    // Clear all glowing highlight effects
    function clearHighlights() {
      document.querySelectorAll('.onboarding-target-highlight').forEach(el => {
        el.classList.remove('onboarding-target-highlight');
      });
    }

    // Floating pointer badges logic
    function isElementVisibleInScrollContainers(el) {
      if (!el) return false;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return false;

      // Recursive check for hidden parent elements/styles/attributes
      let curr = el;
      while (curr && curr !== document.body) {
        const style = window.getComputedStyle(curr);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        if (curr.hidden || curr.hasAttribute('hidden') || curr.getAttribute('aria-hidden') === 'true' || curr.classList.contains('hidden')) return false;
        curr = curr.parentElement;
      }

      let parent = el.parentElement;
      while (parent && parent !== document.body) {
        const parentStyle = window.getComputedStyle(parent);
        const overflowY = parentStyle.overflowY;
        const overflowX = parentStyle.overflowX;

        if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden' ||
            overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'hidden') {
          const parentRect = parent.getBoundingClientRect();
          const buffer = 2; // small tolerance buffer

          if (rect.bottom < parentRect.top + buffer ||
              rect.top > parentRect.bottom - buffer ||
              rect.right < parentRect.left + buffer ||
              rect.left > parentRect.right - buffer) {
            return false;
          }
        }
        parent = parent.parentElement;
      }
      return true;
    }

    function createPointerBadge(targetEl, text, direction, preventScroll = false) {
      if (!targetEl) return;

      // Auto-scroll target element smoothly into view if needed
      if (!preventScroll) {
        try {
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (e) {
          console.warn('Scroll failed:', e);
        }
      }

      const badge = document.createElement('div');
      badge.className = `onboarding-pointer-badge direction-${direction}`;
      badge.textContent = text;

      document.body.appendChild(badge);

      activePointerBadges.push({
        element: badge,
        targetEl: targetEl,
        direction: direction
      });

      positionAllBadges();
    }

    function clearPointerBadges() {
      activePointerBadges.forEach(item => {
        if (item.element && item.element.parentNode) {
          item.element.parentNode.removeChild(item.element);
        }
      });
      activePointerBadges = [];
    }

    function positionAllBadges() {
      activePointerBadges.forEach(item => {
        if (!item.targetEl || !item.element) return;

        const rect = item.targetEl.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) {
          item.element.style.display = 'none';
          return;
        }

        // Check if element is actually visible within its scrollable container boundaries
        if (!isElementVisibleInScrollContainers(item.targetEl)) {
          item.element.style.display = 'none';
          return;
        }

        item.element.style.display = 'flex';

        // Account for current page scrolling coordinates
        const targetTop = rect.top + window.scrollY;
        const targetLeft = rect.left + window.scrollX;

        const badgeRect = item.element.getBoundingClientRect();
        let top = 0;
        let left = 0;
        const offset = 14;

        switch (item.direction) {
          case 'left': // pointing left, badge is on the right of target
            top = targetTop + (rect.height - badgeRect.height) / 2;
            left = targetLeft + rect.width + offset;
            break;
          case 'right': // pointing right, badge is on the left of target
            top = targetTop + (rect.height - badgeRect.height) / 2;
            left = targetLeft - badgeRect.width - offset;
            break;
          case 'top': // pointing up, badge is below target
            top = targetTop + rect.height + offset;
            left = targetLeft + (rect.width - badgeRect.width) / 2;
            break;
          case 'bottom': // pointing down, badge is above target
            top = targetTop - badgeRect.height - offset;
            left = targetLeft + (rect.width - badgeRect.width) / 2;
            break;
        }

        // --- Viewport Bounds Containment & Clamping (Premium Responsive Behavior) ---
        const padding = 10;
        const minLeft = window.scrollX + padding;
        const maxLeft = window.scrollX + window.innerWidth - badgeRect.width - padding;
        const minTop = window.scrollY + padding;
        const maxTop = window.scrollY + window.innerHeight - badgeRect.height - padding;

        left = Math.max(minLeft, Math.min(left, maxLeft));
        top = Math.max(minTop, Math.min(top, maxTop));

        item.element.style.top = `${top}px`;
        item.element.style.left = `${left}px`;
      });
    }

    // Register dynamic position listeners once
    window.addEventListener('resize', positionAllBadges, { passive: true });
    document.addEventListener('scroll', positionAllBadges, { capture: true, passive: true });

    // Toggle minimize/expand logic
    const minimizeBtn = document.getElementById('onboarding-minimize-btn');
    const collapsedIndicator = card ? card.querySelector('.onboarding-collapsed-indicator') : null;

    function toggleMinimize(forceExpand = false) {
      if (!card) return;
      let isCollapsed;
      if (forceExpand) {
        card.classList.remove('collapsed');
        isCollapsed = false;
      } else {
        isCollapsed = card.classList.toggle('collapsed');
      }

      if (minimizeBtn) {
        minimizeBtn.textContent = isCollapsed ? '+' : '−';
        minimizeBtn.title = isCollapsed ? 'Expand guide' : 'Minimize guide';
      }
      if (collapsedIndicator) {
        collapsedIndicator.style.display = isCollapsed ? 'flex' : 'none';
      }
      // Re-position badges in case layout shifted slightly
      setTimeout(positionAllBadges, 200);
    }

    if (minimizeBtn) {
      minimizeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMinimize();
      });
    }

    if (collapsedIndicator) {
      collapsedIndicator.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMinimize();
      });
    }

    // Manage steps
    function updateOnboardingState(step, forceExpand = true) {
      currentStep = step;

      // Apply card placement classes for premium UX positioning
      card.className = `onboarding-card step-${step}`;

      // Update visible step panel
      overlay.querySelectorAll('.onboarding-step').forEach(panel => {
        const stepNum = parseInt(panel.getAttribute('data-step'), 10);
        panel.hidden = stepNum !== step;
      });

      // Update dots indicator
      overlay.querySelectorAll('.onboarding-dot').forEach(dot => {
        const dotNum = parseInt(dot.getAttribute('data-step-dot'), 10);
        dot.classList.toggle('active', dotNum === step);
      });

      // Clear previous highlights & pointer badges
      clearHighlights();
      clearPointerBadges();

      // Set button states
      prevBtn.disabled = step === 1;
      applyTranslations();

      // Update collapsed text indicator
      const collapsedTextEl = document.getElementById('onboarding-collapsed-text');
      if (collapsedTextEl) {
        const languageEl = document.getElementById('runtime_language');
        const lang = (languageEl && languageEl.value === 'KO') ? 'KO' : 'EN';
        if (lang === 'KO') {
          collapsedTextEl.textContent = `✨ 도움말 ${step}/${totalSteps}`;
        } else {
          collapsedTextEl.textContent = `✨ Help ${step}/${totalSteps}`;
        }
      }

      // Update collapsed buttons states
      const collapsedPrevBtn = document.getElementById('onboarding-collapsed-prev-btn');
      const collapsedNextBtn = document.getElementById('onboarding-collapsed-next-btn');
      if (collapsedPrevBtn) {
        collapsedPrevBtn.disabled = step === 1;
        collapsedPrevBtn.classList.toggle('disabled', step === 1);
      }
      if (collapsedNextBtn) {
        collapsedNextBtn.disabled = step === totalSteps;
        collapsedNextBtn.classList.toggle('disabled', step === totalSteps);
      }

      // Auto-expand card on step change to show new instructions if forced
      if (forceExpand) {
        toggleMinimize(true);
      } else {
        setTimeout(positionAllBadges, 50);
      }

      // Run step actions
      if (step === 1) {
        overlay.classList.remove('minimal-backdrop');
        // Welcome step: Make sure settings modal is closed
        if (window.closeSettingsModal) {
          window.closeSettingsModal({ restore: true });
        } else {
          const settingsModalEl = document.getElementById('settings-modal');
          if (settingsModalEl) {
            settingsModalEl.hidden = true;
            settingsModalEl.setAttribute('aria-hidden', 'true');
          }
        }
      }
      else {
        overlay.classList.add('minimal-backdrop');
      }

      if (step === 2) {
        // Step 2: Models by Runtime Role
        // 1. Open settings
        if (window.openSettingsModal) {
          window.openSettingsModal().then(() => {
            // 2. Select roles tab
            if (window.setSettingsTab) {
              window.setSettingsTab('roles');
            }
            // 3. Highlight the roles tab. Full-panel glow clips against the modal scrollbar.
            const tabRoles = document.getElementById('settings-tab-roles');
            if (tabRoles) tabRoles.classList.add('onboarding-target-highlight');

            // Robust multi-phase scroll enforcement to prevent any async hydration jumps
            const enforceScrollTop = () => {
              const settingsScrollBody = document.querySelector('#settings-modal .modal-scroll-body');
              if (settingsScrollBody) {
                settingsScrollBody.scrollTop = 0;
              }
            };
            enforceScrollTop();
            const scrollInterval = setInterval(enforceScrollTop, 50);
            setTimeout(() => clearInterval(scrollInterval), 1500);

            // 4. Create pointer badges with preventScroll = true to prevent scroll hijacking
            setTimeout(() => {
              const plannerTitle = document.getElementById('settings-planner-title');
              const agentCount = document.getElementById('planner_agent_count');
              const backend = document.getElementById('planner_backend');

              if (plannerTitle) {
                createPointerBadge(plannerTitle, getOnboardingText('badgeRuntimeTitle'), 'bottom', true);
              }
              if (agentCount) {
                createPointerBadge(agentCount, getOnboardingText('badgeMaxExecutions'), 'top', true);
              }
              if (backend) {
                createPointerBadge(backend, getOnboardingText('badgeAssistantModel'), 'bottom', true);
              }
            }, 100);
          }).catch(e => console.error('Failed to open settings modal:', e));
        }
      }
      else if (step === 3) {
        // Step 3: Slack Integration
        const settingsModalEl = document.getElementById('settings-modal');
        const openPromise = (settingsModalEl && settingsModalEl.hidden && window.openSettingsModal)
          ? window.openSettingsModal()
          : Promise.resolve();

        openPromise.then(() => {
          // 2. Select slack tab
          if (window.setSettingsTab) {
            window.setSettingsTab('slack');
          }
          // 3. Highlight the Slack tab. Full-panel glow clips against the modal scrollbar.
          const tabSlack = document.getElementById('settings-tab-slack');
          if (tabSlack) tabSlack.classList.add('onboarding-target-highlight');

          // Robust multi-phase scroll enforcement to prevent any async hydration jumps
          const enforceScrollTop = () => {
            const settingsScrollBody = document.querySelector('#settings-modal .modal-scroll-body');
            if (settingsScrollBody) {
              settingsScrollBody.scrollTop = 0;
            }
          };
          enforceScrollTop();
          const scrollInterval = setInterval(enforceScrollTop, 50);
          setTimeout(() => clearInterval(scrollInterval), 1500);

          // 5. Create pointer badges with preventScroll = true to keep scroll at top
          setTimeout(() => {
            const slackEnabled = document.querySelector('label.settings-switch[for="slack_enabled"]');
            const slackBotToken = document.getElementById('slack_bot_token');
            const slackChannel = document.getElementById('slack_default_channel');
            const testSlackSettings = document.getElementById('test-slack-settings');

            if (slackEnabled) {
              createPointerBadge(slackEnabled, getOnboardingText('badgeSlackEnable'), 'right', true);
            }
            if (slackBotToken) {
              createPointerBadge(slackBotToken, getOnboardingText('badgeSlackToken'), 'bottom', true);
            }
            if (slackChannel) {
              createPointerBadge(slackChannel, getOnboardingText('badgeSlackChannel'), 'bottom', true);
            }
            if (testSlackSettings) {
              createPointerBadge(testSlackSettings, getOnboardingText('badgeSlackTest'), 'bottom', true);
            }

            // 6. Dynamic overlap detection — reposition card to bottom-right if it overlaps with any badge
            //    Always simulate the left-position rect to avoid infinite toggle loops.
            const repositionIfOverlapping = (instant) => {
              if (currentStep !== 3) return;
              positionAllBadges();
              // Compute where the card *would* be in its default left position
              const cardWidth = card.offsetWidth;
              const cardHeight = card.offsetHeight;
              const simLeft = 30; // matches CSS: left: 30px
              const simBottom = 30; // matches CSS: bottom: 30px
              const simTop = window.innerHeight - simBottom - cardHeight;
              const simRect = {
                left: simLeft,
                right: simLeft + cardWidth,
                top: simTop,
                bottom: simTop + cardHeight
              };
              const margin = 12; // extra breathing room
              let overlaps = false;
              for (const item of activePointerBadges) {
                if (!item.element || item.element.style.display === 'none') continue;
                const bRect = item.element.getBoundingClientRect();
                if (!(simRect.right + margin < bRect.left ||
                      simRect.left - margin > bRect.right ||
                      simRect.bottom + margin < bRect.top ||
                      simRect.top - margin > bRect.bottom)) {
                  overlaps = true;
                  break;
                }
              }
              const shouldReposition = overlaps;
              const alreadyRepositioned = card.classList.contains('reposition-right');
              if (shouldReposition === alreadyRepositioned) return; // no change needed
              if (instant) {
                // Suppress transition for instant initial placement
                card.style.transition = 'none';
              }
              if (shouldReposition) {
                card.classList.add('reposition-right');
              } else {
                card.classList.remove('reposition-right');
              }
              if (instant) {
                // Force reflow then restore transition
                card.offsetHeight;
                requestAnimationFrame(() => { card.style.transition = ''; });
              }
            };
            // Run on next animation frame for fastest possible initial placement
            requestAnimationFrame(() => repositionIfOverlapping(true));
            // Re-check on resize while Step 3 is active (animated)
            const resizeHandler = () => {
              if (currentStep !== 3) {
                window.removeEventListener('resize', resizeHandler);
                return;
              }
              repositionIfOverlapping(false);
            };
            window.addEventListener('resize', resizeHandler, { passive: true });
          }, 100);
        }).catch(e => console.error('Failed to open settings modal in step 3:', e));
      }
      else if (step === 4) {
        // Step 4: New Request Button
        // 1. Close settings modal
        if (window.closeSettingsModal) {
          window.closeSettingsModal({ restore: true });
        } else {
          const settingsModalEl = document.getElementById('settings-modal');
          if (settingsModalEl) {
            settingsModalEl.hidden = true;
            settingsModalEl.setAttribute('aria-hidden', 'true');
          }
        }
        // 2. Close request composer modal if open (to see open-composer button clearly)
        const cancelBtn = document.getElementById('cancel-composer');
        if (cancelBtn) {
          cancelBtn.click();
        } else {
          const reqModal = document.getElementById('request-modal');
          if (reqModal) {
            reqModal.hidden = true;
            reqModal.setAttribute('aria-hidden', 'true');
          }
        }
        // 3. Highlight header button "New request"
        const newRequestBtn = document.getElementById('open-composer');
        if (newRequestBtn) {
          newRequestBtn.classList.add('onboarding-target-highlight');

          setTimeout(() => {
            createPointerBadge(newRequestBtn, getOnboardingText('badgeNewRequest'), 'bottom');
          }, 100);
        }
      }
      else if (step === 5) {
        // Step 5: Target Project Selection
        // 1. Open request composer modal if not open
        const reqModal = document.getElementById('request-modal');
        const openPromise = (reqModal && reqModal.hidden)
          ? new Promise(resolve => {
              const openBtn = document.getElementById('open-composer');
              if (openBtn) {
                openBtn.click();
                setTimeout(resolve, 300);
              } else {
                resolve();
              }
            })
          : Promise.resolve();

        openPromise.then(() => {
          // 2. Make sure we're NOT on the assistant tab — keep the request basics visible
          //    The target_repo field is in the top "Request basics" section, not in a tab panel

          // 3. Scroll the modal to show the target repo field
          const scrollToTargetRepo = () => {
            const requestScrollBody = document.querySelector('#request-modal .modal-scroll-body');
            if (requestScrollBody) {
              requestScrollBody.scrollTop = 0;
            }
          };
          scrollToTargetRepo();
          setTimeout(scrollToTargetRepo, 100);

          // 4. Highlight target repo and base branch fields
          const targetRepoField = document.getElementById('target_repo');
          const baseBranchField = document.getElementById('base_branch');
          if (targetRepoField) {
            const targetRepoGroup = targetRepoField.closest('.field');
            if (targetRepoGroup) targetRepoGroup.classList.add('onboarding-target-highlight');
          }
          if (baseBranchField) {
            const baseBranchGroup = baseBranchField.closest('.field');
            if (baseBranchGroup) baseBranchGroup.classList.add('onboarding-target-highlight');
          }

          // 5. Create pointer badges
          setTimeout(() => {
            const targetRepo = document.getElementById('target_repo');
            const baseBranch = document.getElementById('base_branch');

            if (targetRepo) {
              createPointerBadge(targetRepo, getOnboardingText('badgeTargetRepo'), 'top', true);
            }
            if (baseBranch) {
              createPointerBadge(baseBranch, getOnboardingText('badgeBaseBranch'), 'top', true);
            }
          }, 100);
        }).catch(e => console.error('Failed to open request composer in step 5:', e));
      }
      else if (step === 6) {
        // Step 6: Draft with Assistant
        // 1. Open request composer modal if not open
        const reqModal = document.getElementById('request-modal');
        const openPromise = (reqModal && reqModal.hidden)
          ? new Promise(resolve => {
              const openBtn = document.getElementById('open-composer');
              if (openBtn) {
                openBtn.click();
                setTimeout(resolve, 300);
              } else {
                resolve();
              }
            })
          : Promise.resolve();

        openPromise.then(() => {
          // 2. Select Assistant tab
          const assistantTab = document.getElementById('request-composer-tab-assistant');
          if (assistantTab) {
            assistantTab.click();
          }

          // 3. Highlight transcript and composer areas
          const transcriptArea = document.getElementById('request-draft-transcript');
          const composerArea = document.getElementById('request-draft-composer');
          if (transcriptArea) transcriptArea.classList.add('onboarding-target-highlight');
          if (composerArea) composerArea.classList.add('onboarding-target-highlight');

          // 4. Scroll request composer modal to the bottom
          const scrollToBottom = () => {
            const requestScrollBody = document.querySelector('#request-modal .modal-scroll-body');
            if (requestScrollBody) {
              requestScrollBody.scrollTop = requestScrollBody.scrollHeight;
            }
          };
          scrollToBottom();
          setTimeout(scrollToBottom, 100);
          setTimeout(scrollToBottom, 300);

          // 5. Create pointer badges with preventScroll = true to prevent scroll jumps
          setTimeout(() => {
            const draftInput = document.getElementById('request-draft-input');
            const draftSend = document.getElementById('send-request-draft');
            const draftTranscript = document.getElementById('request-draft-transcript');

            if (draftInput) {
              createPointerBadge(draftInput, getOnboardingText('badgeRequestDraftInput'), 'bottom', true);
            }
            if (draftSend) {
              createPointerBadge(draftSend, getOnboardingText('badgeRequestDraftSend'), 'left', true);
            }
            if (draftTranscript) {
              createPointerBadge(draftTranscript, getOnboardingText('badgeRequestDraftTranscript'), 'bottom', true);
            }
          }, 100);
        }).catch(e => console.error('Failed to open request composer in step 6:', e));
      }
    }

    // Terminate onboarding and save state
    function finishOnboarding() {
      clearHighlights();
      clearPointerBadges();
      overlay.classList.remove('minimal-backdrop');
      overlay.hidden = true;
      localStorage.setItem('kanban_onboarding_completed', 'true');
    }

    // Attach Event Listeners
    nextBtn.addEventListener('click', () => {
      if (currentStep < totalSteps) {
        updateOnboardingState(currentStep + 1);
      } else {
        finishOnboarding();
      }
    });

    prevBtn.addEventListener('click', () => {
      if (currentStep > 1) {
        updateOnboardingState(currentStep - 1);
      }
    });

    skipBtn.addEventListener('click', () => {
      finishOnboarding();
    });

    // Wire up collapsed navigation chevron controls with propagation blocking
    const collapsedPrevBtn = document.getElementById('onboarding-collapsed-prev-btn');
    const collapsedNextBtn = document.getElementById('onboarding-collapsed-next-btn');

    if (collapsedPrevBtn) {
      collapsedPrevBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (currentStep > 1) {
          updateOnboardingState(currentStep - 1, false);
        }
      });
    }

    if (collapsedNextBtn) {
      collapsedNextBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (currentStep < totalSteps) {
          updateOnboardingState(currentStep + 1, false);
        }
      });
    }

    // Listen to UI language switch changes to apply translation live
    const languageEl = document.getElementById('runtime_language');
    if (languageEl) {
      languageEl.addEventListener('change', applyTranslations);
    }

    // Observe modal and tab panel visibility changes to update pointer badges dynamically
    const settingsModal = document.getElementById('settings-modal');
    const requestModal = document.getElementById('request-modal');
    if (settingsModal || requestModal) {
      const observer = new MutationObserver(() => {
        positionAllBadges();
      });
      const config = {
        attributes: true,
        childList: true,
        subtree: true,
        attributeFilter: ['hidden', 'style', 'class', 'aria-hidden']
      };
      if (settingsModal) observer.observe(settingsModal, config);
      if (requestModal) observer.observe(requestModal, config);
    }

    // Expose a public function to restart or start onboarding
    window.startOnboarding = function() {
      overlay.hidden = false;
      updateOnboardingState(1);
    };

    // Hook up the settings restart button if it exists
    const restartBtn = document.getElementById('restart-onboarding-btn');
    if (restartBtn) {
      restartBtn.addEventListener('click', () => {
        // First close settings modal so the user can see the onboarding wizard clearly
        if (window.closeSettingsModal) {
          window.closeSettingsModal({ restore: true });
        } else {
          const settingsModalEl = document.getElementById('settings-modal');
          if (settingsModalEl) {
            settingsModalEl.hidden = true;
            settingsModalEl.setAttribute('aria-hidden', 'true');
          }
        }
        window.startOnboarding();
      });
    }

    // Hook up the header open-onboarding button if it exists
    const openOnboardingBtn = document.getElementById('open-onboarding');
    if (openOnboardingBtn) {
      openOnboardingBtn.addEventListener('click', () => {
        // First close settings modal so the user can see the onboarding wizard clearly
        if (window.closeSettingsModal) {
          window.closeSettingsModal({ restore: true });
        } else {
          const settingsModalEl = document.getElementById('settings-modal');
          if (settingsModalEl) {
            settingsModalEl.hidden = true;
            settingsModalEl.setAttribute('aria-hidden', 'true');
          }
        }
        window.startOnboarding();
      });
    }

    // Launch onboarding after a tiny delay to allow smooth page load rendering (only if not completed)
    const isOnboardingCompleted = localStorage.getItem('kanban_onboarding_completed') === 'true';
    if (!isOnboardingCompleted) {
      setTimeout(() => {
        window.startOnboarding();
      }, 600);
    }
  });
})();
