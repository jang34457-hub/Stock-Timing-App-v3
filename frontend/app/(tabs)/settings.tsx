import { useCallback, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useFocusEffect } from 'expo-router';

import { Screen } from '@/components/Screen';
import { api, getApiKey, getApiUrl, pingHealth, saveApiConfig } from '@/lib/api';

const DEFAULTS = { x1: '10', x2: '20', y1: '10', y2: '20' };

type Form = {
  phone: string;
  x1: string;
  x2: string;
  y1: string;
  y2: string;
  buy_alert: boolean;
  sell_alert: boolean;
};

export default function SettingsScreen() {
  const [form, setForm] = useState<Form | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState('');
  const [smsMsg, setSmsMsg] = useState('');
  const [smsBusy, setSmsBusy] = useState(false);
  const [liveSms, setLiveSms] = useState(false);
  const [serverUrl, setServerUrl] = useState(getApiUrl());
  const [serverKey, setServerKey] = useState(getApiKey());
  const [pingMsg, setPingMsg] = useState('');
  const [pingBusy, setPingBusy] = useState(false);

  const loadRemote = useCallback(() => {
    api
      .getSettings()
      .then((data) => {
        setForm({
          phone: data.phone_number ?? '',
          x1: String(data.x1 ?? 10),
          x2: String(data.x2 ?? 20),
          y1: String(data.y1 ?? 10),
          y2: String(data.y2 ?? 20),
          buy_alert: data.buy_alert,
          sell_alert: data.sell_alert,
        });
        setLoadError(null);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : '설정을 불러오지 못했습니다.'))
      .finally(() => setLoading(false));
  }, []);

  useFocusEffect(
    useCallback(() => {
      setServerUrl(getApiUrl());
      setServerKey(getApiKey());
      loadRemote();
    }, [loadRemote])
  );

  async function connectServer() {
    setPingMsg('');
    setSaveError('');
    setPingBusy(true);
    try {
      await saveApiConfig(serverUrl, serverKey);
      const health = await pingHealth();
      setPingMsg(health.ok ? `연결됨 (${health.env ?? 'ok'}) · ${getApiUrl()}` : '응답이 올바르지 않습니다.');
      loadRemote();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '서버 연결에 실패했습니다.');
    } finally {
      setPingBusy(false);
    }
  }

  async function save() {
    if (!form) return;
    setSaved('');
    setSaveError('');
    const x1 = Number(form.x1);
    const x2 = Number(form.x2);
    const y1 = Number(form.y1);
    const y2 = Number(form.y2);
    if (![x1, x2, y1, y2].every((n) => Number.isFinite(n) && n > 0)) {
      setSaveError('X1, X2, Y1, Y2는 0보다 큰 숫자여야 합니다.');
      return;
    }
    if (x2 <= x1 || y2 <= y1) {
      setSaveError('X2는 X1보다, Y2는 Y1보다 커야 합니다.');
      return;
    }
    try {
      const next = await api.saveSettings({
        phone_number: form.phone.trim() || null,
        x1,
        x2,
        y1,
        y2,
        buy_alert: form.buy_alert,
        sell_alert: form.sell_alert,
      });
      setForm({
        ...form,
        phone: next.phone_number ?? '',
        x1: String(next.x1),
        x2: String(next.x2),
        y1: String(next.y1),
        y2: String(next.y2),
      });
      setSaved('저장했습니다. 종목 상세 기준가에 바로 반영됩니다.');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '저장에 실패했습니다.');
    }
  }

  async function sendTestSms() {
    if (!form) return;
    setSmsMsg('');
    setSaveError('');
    const phone = form.phone.trim();
    if (!phone) {
      setSaveError('테스트 SMS를 보내려면 전화번호를 입력하세요.');
      return;
    }
    setSmsBusy(true);
    try {
      const result = await api.testSms(phone, liveSms);
      setSmsMsg(
        result.provider === 'test' || result.status === 'test'
          ? `테스트 모드: ${result.to}로 보내지 않고 서버 로그에 기록했습니다. 실제 수신은 Solapi + 아래 스위치를 켜세요.`
          : `${result.to}로 테스트 SMS를 보냈습니다.`
      );
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '테스트 SMS에 실패했습니다.');
    } finally {
      setSmsBusy(false);
    }
  }

  if (!form) {
    return (
      <Screen loading={loading} error={loadError} onRetry={loadRemote}>
        <ServerBlock
          serverUrl={serverUrl}
          serverKey={serverKey}
          pingBusy={pingBusy}
          pingMsg={pingMsg}
          onUrl={setServerUrl}
          onKey={setServerKey}
          onConnect={connectServer}
        />
        {saveError ? <Text style={styles.err}>{saveError}</Text> : null}
      </Screen>
    );
  }

  return (
    <Screen loading={loading} error={null} onRetry={loadRemote}>
      <Text style={styles.title}>설정</Text>
      <Text style={styles.meta}>스마트폰에서는 127.0.0.1 대신 PC의 LAN IP를 넣습니다.</Text>

      <ServerBlock
        serverUrl={serverUrl}
        serverKey={serverKey}
        pingBusy={pingBusy}
        pingMsg={pingMsg}
        onUrl={setServerUrl}
        onKey={setServerKey}
        onConnect={connectServer}
      />

      <Text style={styles.section}>전화번호</Text>
      <TextInput
        style={styles.input}
        keyboardType="phone-pad"
        value={form.phone}
        onChangeText={(phone) => setForm({ ...form, phone })}
        placeholder="01012345678"
        autoComplete="tel"
      />

      <Text style={styles.section}>매도 (최근 3개월 MA3 최고점 대비)</Text>
      <View style={styles.grid}>
        <Field
          label="X1 %"
          hint="기본 10"
          value={form.x1}
          onChange={(x1) => setForm({ ...form, x1 })}
        />
        <Field
          label="X2 %"
          hint="기본 20"
          value={form.x2}
          onChange={(x2) => setForm({ ...form, x2 })}
        />
      </View>

      <Text style={styles.section}>매수 (최근 3개월 MA3 최저점 대비)</Text>
      <View style={styles.grid}>
        <Field
          label="Y1 %"
          hint="기본 10"
          value={form.y1}
          onChange={(y1) => setForm({ ...form, y1 })}
        />
        <Field
          label="Y2 %"
          hint="기본 20"
          value={form.y2}
          onChange={(y2) => setForm({ ...form, y2 })}
        />
      </View>

      <Text style={styles.section}>SMS ON/OFF</Text>
      <Text style={styles.meta}>관심종목 알림과 함께 켜야 문자가 갑니다.</Text>
      <View style={styles.switchRow}>
        <Text>매수 알림</Text>
        <Switch
          value={form.buy_alert}
          onValueChange={(buy_alert) => setForm({ ...form, buy_alert })}
        />
      </View>
      <View style={styles.switchRow}>
        <Text>매도 알림</Text>
        <Switch
          value={form.sell_alert}
          onValueChange={(sell_alert) => setForm({ ...form, sell_alert })}
        />
      </View>

      <Pressable style={styles.ghost} onPress={() => setForm({ ...form, ...DEFAULTS })}>
        <Text style={styles.ghostText}>기본값 10 / 20 으로</Text>
      </Pressable>
      <Pressable style={styles.button} onPress={save}>
        <Text style={styles.buttonText}>저장</Text>
      </Pressable>
      <View style={styles.switchRow}>
        <Text>실제 문자로 테스트 (Solapi)</Text>
        <Switch value={liveSms} onValueChange={setLiveSms} />
      </View>
      <Pressable
        style={[styles.ghostBtn, smsBusy && styles.disabled]}
        onPress={sendTestSms}
        disabled={smsBusy}>
        <Text style={styles.ghostText}>{smsBusy ? '보내는 중…' : '테스트 SMS'}</Text>
      </Pressable>
      {saveError ? <Text style={styles.err}>{saveError}</Text> : null}
      {saved ? <Text style={styles.ok}>{saved}</Text> : null}
      {smsMsg ? <Text style={styles.ok}>{smsMsg}</Text> : null}
    </Screen>
  );
}

function ServerBlock({
  serverUrl,
  serverKey,
  pingBusy,
  pingMsg,
  onUrl,
  onKey,
  onConnect,
}: {
  serverUrl: string;
  serverKey: string;
  pingBusy: boolean;
  pingMsg: string;
  onUrl: (v: string) => void;
  onKey: (v: string) => void;
  onConnect: () => void;
}) {
  return (
    <>
      <Text style={styles.section}>서버 연결</Text>
      <Text style={styles.hint}>예: http://192.168.0.10:8000</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        autoCorrect={false}
        value={serverUrl}
        onChangeText={onUrl}
        placeholder="http://192.168.0.10:8000"
      />
      <Text style={styles.label}>API 키 (운영 서버만)</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        autoCorrect={false}
        value={serverKey}
        onChangeText={onKey}
        placeholder="비어 있으면 생략"
      />
      <Pressable style={[styles.ghostBtn, pingBusy && styles.disabled]} onPress={onConnect} disabled={pingBusy}>
        <Text style={styles.ghostText}>{pingBusy ? '연결 중…' : '서버 연결 확인'}</Text>
      </Pressable>
      {pingMsg ? <Text style={styles.ok}>{pingMsg}</Text> : null}
    </>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>
        {label} <Text style={styles.hint}>{hint}</Text>
      </Text>
      <TextInput
        style={styles.input}
        keyboardType="decimal-pad"
        value={value}
        onChangeText={onChange}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontWeight: '700' },
  meta: { color: '#667085', marginBottom: 4 },
  section: { fontSize: 15, fontWeight: '700', marginTop: 12 },
  label: { fontSize: 13, color: '#344054', marginTop: 8 },
  hint: { color: '#98a2b3', fontWeight: '400' },
  input: {
    borderWidth: 1,
    borderColor: '#d0d5dd',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#fff',
  },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  field: { flex: 1, minWidth: '45%' },
  switchRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
  },
  ghost: { paddingVertical: 8, alignItems: 'center' },
  ghostBtn: {
    borderWidth: 1,
    borderColor: '#175cd3',
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
  },
  ghostText: { color: '#175cd3', fontWeight: '600' },
  disabled: { opacity: 0.5 },
  button: {
    backgroundColor: '#175cd3',
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
  },
  buttonText: { color: '#fff', fontWeight: '700' },
  ok: { color: '#067647' },
  err: { color: '#b42318' },
});
