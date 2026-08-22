import 'dart:async';
import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class AuditScreen extends StatefulWidget {
  const AuditScreen({super.key});
  @override
  State<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends State<AuditScreen> {
  final _urlController = TextEditingController();
  List _history = [];
  bool _loadingHistory = true;
  bool _running = false;
  String? _runningId;
  Map? _status;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _loadHistory();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _loadHistory() async {
    setState(() => _loadingHistory = true);
    try {
      _history = (await Api.instance.get('/api/audit/history') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loadingHistory = false);
  }

  Future<void> _start() async {
    final url = _urlController.text.trim();
    if (url.isEmpty) return;
    setState(() { _running = true; _status = null; });
    final res = await Api.instance.post('/api/audit/start', {'url': url});
    if (res?['success'] != true) {
      setState(() => _running = false);
      return;
    }
    _runningId = res['audit_id'];
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _checkStatus());
  }

  Future<void> _checkStatus() async {
    if (_runningId == null) return;
    final res = await Api.instance.get('/api/audit/status/$_runningId');
    if (!mounted) return;
    setState(() => _status = res as Map?);
    final status = _status?['status'];
    if (status == 'done' || status == 'error') {
      _poll?.cancel();
      setState(() => _running = false);
      _loadHistory();
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Website-Audit')),
      body: RefreshIndicator(
        onRefresh: _loadHistory,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            GlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Neue Analyse', style: TextStyle(color: c.text, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  TextField(
                    controller: _urlController,
                    style: TextStyle(color: c.text),
                    decoration: const InputDecoration(hintText: 'https://example.com'),
                  ),
                  const SizedBox(height: 10),
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton(
                      onPressed: _running ? null : _start,
                      child: Text(_running ? 'Läuft... (${_status?['progress'] ?? 0}%)' : 'Analyse starten'),
                    ),
                  ),
                  if (_status != null && _status!['status'] != null) ...[
                    const SizedBox(height: 10),
                    if (_status!['status'] == 'error')
                      Text('Fehler: ${_status!['error'] ?? 'unbekannt'}', style: const TextStyle(color: danger, fontSize: 12))
                    else if (_status!['summary'] != null)
                      Text(_status!['summary'].toString(), style: TextStyle(color: c.dim, fontSize: 12)),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 20),
            Text('Verlauf', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            if (_loadingHistory)
              const Center(child: CircularProgressIndicator())
            else if (_history.isEmpty)
              GlassCard(child: Text('Noch keine Analysen.', style: TextStyle(color: c.dim)))
            else
              ..._history.map((h) => Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: GlassCard(
                      child: Row(children: [
                        Icon(h['status'] == 'done' ? Icons.check_circle : Icons.error_outline, color: h['status'] == 'done' ? good : danger, size: 18),
                        const SizedBox(width: 10),
                        Expanded(child: Text(h['url']?.toString() ?? '', style: TextStyle(color: c.text, fontSize: 12), overflow: TextOverflow.ellipsis)),
                      ]),
                    ),
                  )),
          ],
        ),
      ),
    );
  }
}
