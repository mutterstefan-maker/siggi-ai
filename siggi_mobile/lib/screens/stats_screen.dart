import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

/// Generic key/value renderer for the stats endpoints - the exact shape of
/// /api/stats/daily and /api/gsc/overview can evolve on the server without
/// breaking this screen.
class StatsScreen extends StatefulWidget {
  const StatsScreen({super.key});
  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen> {
  Map _daily = {};
  Map? _gsc;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _daily = (await Api.instance.get('/api/stats/daily') as Map?) ?? {};
    } catch (_) {}
    try {
      _gsc = await Api.instance.get('/api/gsc/overview') as Map?;
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Statistiken')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Text('Tagesstatistik', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  GlassCard(child: _kvGrid(_daily, c)),
                  const SizedBox(height: 20),
                  Text('Google Search Console', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  if (_gsc == null || _gsc!['error'] != null)
                    GlassCard(child: Text('Nicht verfügbar.', style: TextStyle(color: c.dim)))
                  else
                    GlassCard(child: _kvGrid((_gsc!['overview'] as Map?) ?? {}, c)),
                ],
              ),
      ),
    );
  }

  Widget _kvGrid(Map data, dynamic c) {
    final entries = data.entries.where((e) => e.value is! Map && e.value is! List).toList();
    if (entries.isEmpty) return Text('Keine Daten.', style: TextStyle(color: c.dim));
    return Wrap(
      spacing: 20, runSpacing: 14,
      children: entries.map((e) => SizedBox(
        width: 130,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${e.value}', style: TextStyle(color: accent, fontSize: 20, fontWeight: FontWeight.w800)),
            Text(e.key.toString(), style: TextStyle(color: c.dim, fontSize: 11)),
          ],
        ),
      )).toList(),
    );
  }
}
